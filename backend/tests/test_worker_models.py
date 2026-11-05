"""Tests that run the real neural networks. They skip themselves when the models or packages are not available."""
import os
import random
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest

from tests.plate_render import car_scene, rand_plate
from worker.pipeline import CameraConfig, CameraPipeline
from worker.plates import vote
from worker.sink import MemorySink
from worker.tracker import Detection

pytestmark = pytest.mark.models
MODEL_DIR = Path(os.environ.get("GI_WORKER_MODEL_DIR", Path.home() / ".cache" / "gateinsight" / "models"))


@pytest.fixture(scope="module")
def yolox():
    path = MODEL_DIR / "yolox_tiny.onnx"
    if not path.is_file():
        pytest.skip("run: python -m scripts.fetch_models")
    from worker.detector import YoloxDetector
    return YoloxDetector(path)


@pytest.fixture(scope="module")
def plate_reader():
    pytest.importorskip("open_image_models")
    pytest.importorskip("fast_plate_ocr")
    try:
        from worker.plates import OnnxPlateReader
        return OnnxPlateReader()
    except Exception as e:                                                  # no network for the first download
        pytest.skip(f"plate models unavailable: {e}")


def test_detector_runs_and_sees_nothing_in_an_empty_scene(yolox):
    assert yolox.detect(np.full((360, 640, 3), 120, np.uint8)) == []
    noise = np.random.default_rng(1).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    assert all(d.label in {"person", "car", "motorcycle", "bus", "truck"} for d in yolox.detect(noise))


def test_detector_accepts_any_picture_shape_and_keeps_boxes_inside_the_frame(yolox):
    for shape in ((360, 640), (1080, 1920), (300, 300), (720, 480)):
        f = np.random.default_rng(2).integers(0, 255, (*shape, 3), dtype=np.uint8)
        for d in yolox.detect(f):
            assert 0 <= d.box[0] <= d.box[2] <= shape[1] and 0 <= d.box[1] <= d.box[3] <= shape[0]


def test_detection_is_fast_enough_for_five_frames_per_second_on_cpu(yolox):
    f = np.full((360, 640, 3), 120, np.uint8)
    yolox.detect(f)
    ms = [(yolox.detect(f), yolox.last_ms)[1] for _ in range(5)]
    assert sorted(ms)[2] < 150                                              # median well under the 200 ms a 5 fps camera allows


def test_plate_reader_reads_rendered_plates(plate_reader):
    rng, nprng = random.Random(11), np.random.default_rng(11)
    ok = total = 0
    for _ in range(12):
        text = rand_plate(rng)
        reads = plate_reader.read(car_scene(text, rng, nprng))
        total += 1
        ok += bool(reads) and max(reads, key=lambda r: r.confidence).text == text
    assert ok / total >= 0.65, f"only {ok}/{total} read exactly"


def test_voting_over_frames_beats_single_reads(plate_reader):
    rng, nprng = random.Random(5), np.random.default_rng(5)
    single = voted = 0
    plates = [rand_plate(rng) for _ in range(8)]
    for text in plates:
        reads = []
        for _ in range(5):                                                 # five frames of the same vehicle, each degraded differently
            r = plate_reader.read(car_scene(text, rng, nprng))
            if r:
                best = max(r, key=lambda x: x.confidence)
                reads.append((best.text, best.confidence))
        single += sum(t == text for t, _ in reads) / max(len(reads), 1)
        voted += bool(reads) and vote(reads)[0] == text
    assert voted / len(plates) >= 0.75
    assert voted / len(plates) >= single / len(plates) - 0.05               # voting is never meaningfully worse than one read


class WholeFrameVehicle:
    """Stands in for the vehicle detector: calls the whole picture one car. The plate models are the real ones."""
    last_ms = 0.0

    def detect(self, frame):
        h, w = frame.shape[:2]
        return [Detection((20.0, 20.0, w - 20.0, h - 20.0), 0.9, "car")]


def test_pipeline_with_the_real_plate_models_emits_the_right_plate(plate_reader):
    rng, nprng = random.Random(21), np.random.default_rng(21)
    results = []
    for _ in range(3):
        text = rand_plate(rng)
        sink = MemorySink()
        pipe = CameraPipeline(CameraConfig("cam", use_anpr=True), WholeFrameVehicle(), plate_reader, sink, min_read_interval=0.0, max_reads=8, min_vehicle_area=0.0)
        for i in range(10):                                                # ten frames, each degraded differently
            pipe.process(car_scene(text, rng, nprng), datetime(2026, 11, 4, 10, 0, i // 5, tzinfo=timezone.utc))
        pipe.finish()
        results.append(len(sink.plates) == 1 and sink.plates[0]["plate_raw"] == text)
    assert sum(results) >= 2, results
