"""Download the detection models the worker needs and check them against pinned hashes.

    python -m scripts.fetch_models                 # yolox_tiny only
    python -m scripts.fetch_models yolox_nano yolox_s

YOLOX is Apache-2.0 licensed (the YOLOv8 family is AGPL-3.0, which is a poor fit for a closed commercial service).
The plate detector and OCR packages download their own weights the first time they run; this script triggers that too.
"""
import hashlib
import sys
import urllib.request

from worker.config import WorkerSettings

BASE = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0"
PINNED = {
    "yolox_nano": "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d",
    "yolox_tiny": "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7",
    "yolox_s": "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063",
}


def fetch(name: str, dest_dir) -> None:
    dest = dest_dir / f"{name}.onnx"
    if dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == PINNED[name]:
        print(f"{name}: already present")
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    print(f"{name}: downloading ...")
    urllib.request.urlretrieve(f"{BASE}/{name}.onnx", tmp)
    got = hashlib.sha256(tmp.read_bytes()).hexdigest()
    if got != PINNED[name]:
        tmp.unlink()
        sys.exit(f"{name}: checksum mismatch (got {got}). The file was NOT installed.")
    tmp.rename(dest)
    print(f"{name}: ok")


def main() -> None:
    names = sys.argv[1:] or ["yolox_tiny"]
    unknown = [n for n in names if n not in PINNED]
    if unknown:
        sys.exit(f"unknown model(s): {unknown}. Choose from {sorted(PINNED)}")
    d = WorkerSettings().model_dir
    for n in names:
        fetch(n, d)
    print("plate models: loading once so they are downloaded ...")
    from worker.plates import OnnxPlateReader
    OnnxPlateReader()
    print("done. Models are in", d)


if __name__ == "__main__":
    main()
