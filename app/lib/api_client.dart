import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

class ApiError implements Exception {
  ApiError(this.status, this.message, {this.code});
  final int status;
  final String message;
  final String? code;
  @override
  String toString() => message;
}

/// Where this device keeps its own sign-in between launches.
///
/// The refresh token is a password in all but name, so it belongs in the platform's protected store (the iOS Keychain, the
/// Android Keystore, the desktop secret service), not in a preferences file that any backup or other app can read.
abstract class TokenStore {
  Future<String?> read();
  Future<void> write(String token);
  Future<void> clear();
}

class PrefsTokenStore implements TokenStore {
  PrefsTokenStore(this._prefs);
  final SharedPreferences _prefs;
  static const _key = 'gi_next_rt';

  @override
  Future<String?> read() async => _prefs.getString(_key);
  @override
  Future<void> write(String token) => _prefs.setString(_key, token);
  @override
  Future<void> clear() => _prefs.remove(_key);
}

/// The protected store, with a preferences fallback for a machine that has no secret service (some Linux desktops, a test
/// harness). The fallback remembers which one worked, so a token written to one is read from the same one.
class SecureTokenStore implements TokenStore {
  SecureTokenStore({TokenStore? fallback}) : _fallback = fallback ?? _PrefsFallback();
  static const _key = 'gi_next_rt';
  // The default Android options are already AES-GCM with RSA-wrapped keys in the Keystore; nothing extra to ask for.
  final FlutterSecureStorage _secure = const FlutterSecureStorage();
  final TokenStore _fallback;
  bool _usingFallback = false;

  @override
  Future<String?> read() async {
    if (!_usingFallback) {
      try {
        return await _secure.read(key: _key).timeout(const Duration(seconds: 3));
      } catch (_) {
        _usingFallback = true;
      }
    }
    return _fallback.read();
  }

  @override
  Future<void> write(String token) async {
    if (!_usingFallback) {
      try {
        await _secure.write(key: _key, value: token).timeout(const Duration(seconds: 3));
        return;
      } catch (_) {
        _usingFallback = true;
      }
    }
    await _fallback.write(token);
  }

  @override
  Future<void> clear() async {
    if (!_usingFallback) {
      try {
        await _secure.delete(key: _key).timeout(const Duration(seconds: 3));
      } catch (_) {
        _usingFallback = true;
      }
    }
    await _fallback.clear();
  }
}

class _PrefsFallback implements TokenStore {
  Future<SharedPreferences>? _prefs;
  Future<SharedPreferences> get _p => _prefs ??= SharedPreferences.getInstance();
  static const _key = 'gi_next_rt';

  @override
  Future<String?> read() async => (await _p).getString(_key);
  @override
  Future<void> write(String token) async => (await _p).setString(_key, token);
  @override
  Future<void> clear() async => (await _p).remove(_key);
}

/// Talks to the server and keeps the person signed in. The access token lives in memory; the refresh token is kept on this
/// device so a reload does not sign them out.
class ApiClient {
  ApiClient({String? base, http.Client? client, SharedPreferences? prefs, TokenStore? tokens, this.timeout = const Duration(seconds: 20)})
      : base = base ?? (kIsWeb ? Uri.base.origin : ''),
        _c = client ?? http.Client(),
        _ownsClient = client == null,
        _tokens = tokens ?? (prefs != null ? PrefsTokenStore(prefs) : SecureTokenStore());

  final String base;
  final http.Client _c;
  final bool _ownsClient;
  final TokenStore _tokens;

  /// How long the server may take before the person is told it is not answering. A screen must not sit on a skeleton forever.
  final Duration timeout;

  /// Carries the one request that is not JSON (an SDP offer for smooth video). It shares this client's base and deadline.
  final Dio _dio = Dio();

  String? _access, _refresh;

  String? _staffToken;
  bool _viewing = false;

  /// The staff sign-in from the last [signIn] that was a staff account, once: the page passes it to the staff console.
  String? takeStaffToken() {
    final t = _staffToken;
    _staffToken = null;
    return t;
  }

  /// Staff looking at a customer's app: a view-only access token for this tab only. Nothing is stored, and it is never renewed.
  void useViewAs(String token) {
    _access = token;
    _refresh = null;
    _viewing = true;
  }

  /// Saves the password someone chose from an invitation link.
  Future<void> setPassword(String token, String password) async {
    _decode(await _timed(() => _c.post(Uri.parse('$base/v1/auth/set-password'), headers: {'Content-Type': 'application/json'}, body: jsonEncode({'token': token, 'password': password}))));
  }

  bool get signedIn => _refresh != null || _access != null;
  String? get refreshToken => _refresh;          // this device's own session, sent when the password is changed so this device stays signed in

  Future<void> restore() async {
    _refresh = await _tokens.read();
  }

  /// Returns null when signed in, or "code" when a staff member must also give an authenticator code.
  Future<String?> signIn(String email, String password, {String? org, String? code}) async {
    final r = await _timed(() => _c.post(Uri.parse('$base/v1/auth/sign-in'),
        headers: {'Content-Type': 'application/json'}, body: jsonEncode({'email': email, 'password': password, if (org != null && org.isNotEmpty) 'tenant': org, if (code != null && code.isNotEmpty) 'code': code})));
    final j = _decode(r);
    if (j is Map && j['next'] == 'code') return 'code';
    if (j is Map && j['kind'] == 'staff') {
      _staffToken = j['refresh_token'] as String?;        // not kept: it is handed to the staff console (see SignInPage) and forgotten
      return 'staff';
    }
    await _store(j as Map);
    return null;
  }

  Future<void> signOut() async {
    final rt = _refresh;
    _access = null;
    _refresh = null;
    await _tokens.clear();
    if (rt != null) {
      try {
        await _timed(() => _c.post(Uri.parse('$base/v1/auth/logout'), headers: {'Content-Type': 'application/json'}, body: jsonEncode({'refresh_token': rt})));
      } catch (_) {}
    }
  }

  Future<void> _store(Map j) async {
    _access = j['access_token'] as String?;
    _refresh = (j['refresh_token'] as String?) ?? _refresh;
    if (_refresh != null) await _tokens.write(_refresh!);
  }

  Future<bool>? _renewing;

  /// Renews the sign-in. When several screens ask at once (a reload starts with no access token, and Home, the tab badge and Settings all load together),
  /// they share ONE renewal. Each sending the same refresh token would look to the server like a stolen token being replayed, and it would end the session.
  Future<bool> _refreshTokens() {
    final running = _renewing;
    if (running != null) return running;
    final f = _renewOnce();
    _renewing = f;
    return f.whenComplete(() => _renewing = null);
  }

  Future<bool> _renewOnce() async {
    if (_viewing) return false;
    final rt = _refresh;
    if (rt == null) return false;
    try {
      final r = await _timed(() => _c.post(Uri.parse('$base/v1/auth/refresh'), headers: {'Content-Type': 'application/json'}, body: jsonEncode({'refresh_token': rt})));
      if (r.statusCode != 200) {
        _access = null;
        _refresh = null;
        await _tokens.clear();
        return false;
      }
      await _store(jsonDecode(r.body) as Map);
      return true;
    } catch (_) {
      return false;
    }
  }

  /// Runs one request with a deadline. A server that has stopped answering becomes a plain error the screen can show, never a spinner that never stops.
  Future<T> _timed<T>(Future<T> Function() go) async {
    try {
      return await go().timeout(timeout);
    } on TimeoutException {
      throw ApiError(0, 'The server is taking too long to answer. Check your connection and try again.', code: 'timeout');
    } on http.ClientException {
      throw ApiError(0, 'Could not reach the server. Check your connection and try again.', code: 'offline');
    }
  }

  dynamic _decode(http.Response r) {
    if (r.statusCode >= 200 && r.statusCode < 300) return r.body.isEmpty ? null : jsonDecode(r.body);
    var msg = 'Something went wrong (${r.statusCode}).';
    String? code;
    try {
      final e = jsonDecode(r.body)['error'];
      msg = (e['message'] as String?) ?? msg;
      code = e['code'] as String?;
    } catch (_) {}
    throw ApiError(r.statusCode, msg, code: code);
  }

  Future<http.Response> raw(String method, String path, {Object? body, Map<String, String>? query, bool retry = true}) async {
    if (_access == null && !await _refreshTokens()) throw ApiError(401, 'Please sign in again.', code: 'signed_out');
    final uri = Uri.parse('$base$path').replace(queryParameters: query);
    final req = http.Request(method, uri)..headers['Authorization'] = 'Bearer $_access';
    if (body != null) {
      req.headers['Content-Type'] = 'application/json';
      req.body = jsonEncode(body);
    }
    final r = await http.Response.fromStream(await _timed(() => _c.send(req)));
    if (r.statusCode == 401 && retry) {
      _access = null;
      if (await _refreshTokens()) return raw(method, path, body: body, query: query, retry: false);
      throw ApiError(401, 'Please sign in again.', code: 'signed_out');
    }
    return r;
  }

  /// A long answer read as it arrives (live video). Throws the server's own message if it says no.
  Future<Stream<List<int>>> stream(String path, {bool retry = true}) async {
    if (_access == null && !await _refreshTokens()) throw ApiError(401, 'Please sign in again.', code: 'signed_out');
    final req = http.Request('GET', Uri.parse('$base$path'))..headers['Authorization'] = 'Bearer $_access';
    // Only the wait for the answer has a deadline; the picture stream itself may stay open for as long as the person watches.
    final r = await _timed(() => _c.send(req));
    if (r.statusCode == 401 && retry) {
      _access = null;
      if (await _refreshTokens()) return stream(path, retry: false);
      throw ApiError(401, 'Please sign in again.', code: 'signed_out');
    }
    if (r.statusCode < 200 || r.statusCode >= 300) _decode(await http.Response.fromStream(r));
    return r.stream;
  }

  /// Sends an SDP offer for smooth live video and returns the server's answer. This is not this app's JSON API: the body and
  /// the reply are plain SDP, so dio carries them as text and the server's own message is still what the person sees on failure.
  Future<String> sdp(String path, String offer) async {
    if (_access == null && !await _refreshTokens()) throw ApiError(401, 'Please sign in again.', code: 'signed_out');
    final Response<String> r;
    try {
      r = await _timed(() => _dio.post<String>('$base$path', data: offer,
          options: Options(headers: {'Authorization': 'Bearer $_access', 'Content-Type': 'application/sdp'},
              responseType: ResponseType.plain, validateStatus: (_) => true)));
    } on DioException catch (e) {
      final slow = e.type == DioExceptionType.connectionTimeout || e.type == DioExceptionType.receiveTimeout || e.type == DioExceptionType.sendTimeout;
      throw ApiError(0, slow ? 'The server is taking too long to answer. Check your connection and try again.' : 'Could not reach the server. Check your connection and try again.',
          code: slow ? 'timeout' : 'offline');
    }
    final status = r.statusCode ?? 0;
    if (status >= 200 && status < 300) return r.data ?? '';
    var msg = 'Smooth video is not available right now.';
    String? code;
    try {
      final e = (jsonDecode(r.data ?? '') as Map)['error'];
      msg = (e['message'] as String?) ?? msg;
      code = e['code'] as String?;
    } catch (_) {}
    throw ApiError(status, msg, code: code);
  }

  /// A file the person asked for (a report), with the name the server gave it.
  Future<({String name, Uint8List bytes})> file(String path, {String fallbackName = 'download'}) async {
    final r = await raw('GET', path);
    if (r.statusCode < 200 || r.statusCode >= 300) _decode(r);
    final m = RegExp(r'filename="?([^";]+)"?').firstMatch(r.headers['content-disposition'] ?? '');
    return (name: m?.group(1) ?? fallbackName, bytes: r.bodyBytes);
  }

  Future<dynamic> call(String method, String path, {Object? body, Map<String, String>? query}) async => _decode(await raw(method, path, body: body, query: query));

  /// Releases the connections this client owns. Called when the app is torn down (and by tests), so a reload does not leave
  /// sockets or a pending renewal behind.
  void close() {
    _dio.close(force: true);
    if (_ownsClient) _c.close();
  }
}
