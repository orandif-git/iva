import 'dart:typed_data';

import 'package:share_plus/share_plus.dart';

/// Phones and desktop apps: the file is handed to the share sheet, where the person can save it to Files, send it on WhatsApp or open it in a spreadsheet app.
Future<void> saveFile(String name, Uint8List bytes, String mime) async {
  await SharePlus.instance.share(ShareParams(files: [XFile.fromData(bytes, name: name, mimeType: mime)], fileNameOverrides: [name]));
}
