// Saving a file the person asked for. On the web the browser downloads it; on a phone it is handed to the share sheet (see save_file_io.dart).
export 'save_file_io.dart' if (dart.library.js_interop) 'save_file_web.dart';
