import 'dart:convert';

import 'package:flutter/foundation.dart';
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

/// Talks to the server and keeps the person signed in. The access token lives in memory; the refresh token is kept on this device so a reload does not sign them out.
class ApiClient {
  ApiClient({String? base, http.Client? client, SharedPreferences? prefs})
      : base = base ?? (kIsWeb ? Uri.base.origin : ''),
        _c = client ?? http.Client(),
        _prefs = prefs;

  final String base;
  final http.Client _c;
  SharedPreferences? _prefs;
  String? _access, _refresh;
  static const _key = 'gi_next_rt';

  bool get signedIn => _refresh != null || _access != null;

  Future<void> restore() async {
    _prefs ??= await SharedPreferences.getInstance();
    _refresh = _prefs!.getString(_key);
  }

  /// Returns null when signed in, or "code" when a staff member must also give an authenticator code.
  Future<String?> signIn(String email, String password, {String? org, String? code}) async {
    final r = await _c.post(Uri.parse('$base/v1/auth/sign-in'),
        headers: {'Content-Type': 'application/json'}, body: jsonEncode({'email': email, 'password': password, if (org != null && org.isNotEmpty) 'tenant': org, if (code != null && code.isNotEmpty) 'code': code}));
    final j = _decode(r);
    if (j is Map && j['next'] == 'code') return 'code';
    if (j is Map && j['kind'] == 'staff') throw ApiError(403, 'This is a staff account. Staff sign in at /staff/.');
    await _store(j as Map);
    return null;
  }

  Future<void> signOut() async {
    final rt = _refresh;
    _access = null;
    _refresh = null;
    await _prefs?.remove(_key);
    if (rt != null) {
      try {
        await _c.post(Uri.parse('$base/v1/auth/logout'), headers: {'Content-Type': 'application/json'}, body: jsonEncode({'refresh_token': rt}));
      } catch (_) {}
    }
  }

  Future<void> _store(Map j) async {
    _access = j['access_token'] as String?;
    _refresh = (j['refresh_token'] as String?) ?? _refresh;
    if (_refresh != null) await _prefs?.setString(_key, _refresh!);
  }

  Future<bool> _refreshTokens() async {
    final rt = _refresh;
    if (rt == null) return false;
    try {
      final r = await _c.post(Uri.parse('$base/v1/auth/refresh'), headers: {'Content-Type': 'application/json'}, body: jsonEncode({'refresh_token': rt}));
      if (r.statusCode != 200) {
        _access = null;
        _refresh = null;
        await _prefs?.remove(_key);
        return false;
      }
      await _store(jsonDecode(r.body) as Map);
      return true;
    } catch (_) {
      return false;
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
    final r = await http.Response.fromStream(await _c.send(req));
    if (r.statusCode == 401 && retry) {
      _access = null;
      if (await _refreshTokens()) return raw(method, path, body: body, query: query, retry: false);
      throw ApiError(401, 'Please sign in again.', code: 'signed_out');
    }
    return r;
  }

  Future<dynamic> call(String method, String path, {Object? body, Map<String, String>? query}) async => _decode(await raw(method, path, body: body, query: query));
}
