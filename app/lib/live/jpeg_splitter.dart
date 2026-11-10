import 'dart:typed_data';

/// Cuts a stream of bytes into JPEG pictures. The server sends them one after another (with a small header between), and a JPEG always ends at the
/// first FF D9, because an FF inside the picture data is always escaped. Chunks from the network can end anywhere, so the rest is kept for the next chunk.
class JpegSplitter {
  Uint8List _buf = Uint8List(0);
  static const _maxBuffer = 4000000;

  List<Uint8List> add(List<int> chunk) {
    final merged = Uint8List(_buf.length + chunk.length)..setRange(0, _buf.length, _buf)..setRange(_buf.length, _buf.length + chunk.length, chunk);
    _buf = merged;
    final out = <Uint8List>[];
    var from = 0;
    while (true) {
      final s = _find(0xD8, from);
      if (s < 0) { from = _buf.isNotEmpty && _buf.last == 0xFF ? _buf.length - 1 : _buf.length; break; }   // keep a lone FF: its partner may be in the next chunk
      final e = _find(0xD9, s + 2);
      if (e < 0) { from = s; break; }
      out.add(Uint8List.sublistView(_buf, s, e + 2));
      from = e + 2;
    }
    _buf = from >= _buf.length ? Uint8List(0) : Uint8List.fromList(_buf.sublist(from));
    if (_buf.length > _maxBuffer) _buf = Uint8List(0);
    return out;
  }

  /// The index of an FF followed by [second], at or after [start], or -1.
  int _find(int second, int start) {
    for (var i = start; i < _buf.length - 1; i++) {
      if (_buf[i] == 0xFF && _buf[i + 1] == second) return i;
    }
    return -1;
  }
}
