import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:ivision_app/api_client.dart';
import 'package:ivision_app/live/live_rules.dart';
import 'package:ivision_app/models.dart';
import 'package:ivision_app/repo.dart';
import 'package:shared_preferences/shared_preferences.dart';

class Fake {
  final calls = <String>[];
  final bodies = <String, dynamic>{};
  int refreshes = 0;
  bool expireOnce = false;

  http.Response handle(http.Request r) {
    final key = '${r.method} ${r.url.path}';
    calls.add(key);
    if (r.body.isNotEmpty) bodies[key] = jsonDecode(r.body);
    http.Response ok(Object o, [int c = 200]) => http.Response(jsonEncode(o), c);
    switch (key) {
      case 'POST /v1/auth/sign-in':
        return ok({'access_token': 'a1', 'refresh_token': 'r1', 'kind': 'customer'});
      case 'POST /v1/auth/refresh':
        refreshes++;
        return ok({'access_token': 'a2', 'refresh_token': 'r2'});
    }
    if (expireOnce && r.headers['Authorization'] == 'Bearer a1') {
      expireOnce = false;
      return http.Response('{"error":{"code":"token_expired","message":"expired"}}', 401);
    }
    switch (key) {
      case 'GET /v1/health-summary':
        return ok({'status': 'attention', 'issues': [{'kind': 'camera_down', 'level': 'critical', 'title': 'Stockroom camera is not sending pictures'}], 'open_alerts': 1,
          'recorders': {'online': 1, 'total': 2}, 'cameras': {'working': 7, 'total': 8}, 'messages': {'sent_24h': 3, 'failed_24h': 0}, 'data': {'newest_seconds_ago': 12}});
      case 'GET /v1/alert-types':
        return ok([
          {'type': 'camera_offline', 'title': 'Camera offline', 'description': 'A camera stops.', 'ai': false, 'enabled': true, 'params': {}, 'settings': {}, 'recipient_ids': ['p1']},
          {'type': 'queue_long', 'title': 'Long queue', 'description': 'Queue.', 'ai': true, 'enabled': false, 'params': {}, 'settings': {}, 'recipient_ids': []},
        ]);
      case 'GET /v1/area-kinds':
        return ok([{'alert': 'queue_long', 'kind': 'queue_area', 'settings': {'length': {'default': 5, 'min': 1, 'max': 1000}, 'minutes': {'default': 3, 'min': 1, 'max': 60}, 'picture_minutes': {'default': 2, 'min': 0, 'max': 60}, 'repeat_minutes': {'default': 15, 'min': 5, 'max': 1440}}}]);
      case 'GET /v1/alerts':
        return ok([{'id': 'x1', 'rule_type': 'queue_long', 'title': '9 waiting', 'body': 'b', 'created_at': '2026-11-10T10:42:00+05:30', 'camera_name': 'Billing queue', 'state': 'open'}]);
      case 'GET /v1/footfall/summary':
        return ok({'people_in': 120, 'people_out': 100, 'net': 20, 'peak_hour': {'bucket': '2026-11-10T17:00:00', 'people_in': 40}, 'cameras': []});
      case 'GET /v1/recorders':
        return ok([{'id': 'r1', 'name': 'Store recorder', 'device_id': 'D1'}]);
      case 'GET /v1/cameras':
        return ok([{'id': 'c1', 'name': 'Front door', 'recorder_id': 'r1', 'enabled': true, 'stream_state': 'streaming'}, {'id': 'c2', 'name': 'Paused', 'recorder_id': 'r1', 'enabled': false, 'stream_state': 'offline'}]);
      case 'GET /v1/cameras/c1/areas':
        return ok([{'id': 'z1', 'kind': 'area_count', 'name': 'Old', 'points': [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]], 'params': {'limit': 5}}]);
      case 'PUT /v1/cameras/c1/areas':
      case 'PUT /v1/alert-types/queue_long':
        return ok({});
      case 'POST /v1/alerts/x1/snooze':
        return ok({});
      case 'POST /v1/cameras/c1/live':
        return ok({'id': 's1', 'smooth': true, 'ice': ['stun:stun.example.test:3478']}, 201);
    }
    return http.Response('{"error":{"code":"not_found","message":"no"}}', 404);
  }
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  Future<(ApiRepository, ApiClient, Fake)> signedIn() async {
    final f = Fake();
    final c = ApiClient(base: 'http://x', client: MockClient((r) async => f.handle(r)), tokens: PrefsTokenStore(await SharedPreferences.getInstance()));
    await c.restore();
    await c.signIn('a@b.test', 'pw');
    return (ApiRepository(c), c, f);
  }

  test('signing in keeps the refresh token on the device and signing out clears it', () async {
    final (_, c, _) = await signedIn();
    expect((await SharedPreferences.getInstance()).getString('gi_next_rt'), 'r1');
    await c.signOut();
    expect((await SharedPreferences.getInstance()).getString('gi_next_rt'), isNull);
    expect(c.signedIn, isFalse);
  });

  test('a staff account is handed to the staff console, not signed in here', () async {
    final c = ApiClient(base: 'http://x', client: MockClient((r) async => http.Response('{"kind":"staff","access_token":"s","refresh_token":"t"}', 200)), tokens: PrefsTokenStore(await SharedPreferences.getInstance()));
    await c.restore();
    expect(await c.signIn('s@b.test', 'pw'), 'staff');
    expect(c.takeStaffToken(), 't', reason: 'handed to the console, once');
    expect(c.takeStaffToken(), isNull);
    expect(c.signedIn, false, reason: 'not signed in to the customer app');
  });

  test('an expired token is refreshed once and the call is retried', () async {
    final (repo, _, f) = await signedIn();
    f.expireOnce = true;
    final h = await repo.health();
    expect(f.refreshes, 1);
    expect(h.ok, isFalse);
    expect(h.issues.single.title, contains('Stockroom'));
    expect(h.camerasWorking, 7);
  });

  test('alerts say whether they are Smart, and times are friendly', () async {
    final (repo, _, _) = await signedIn();
    final a = (await repo.alerts()).single;
    expect(a.smart, isTrue);
    expect(a.where, 'Billing queue');
    expect(friendlyWhen('2026-11-10T10:42:00', now: DateTime(2026, 11, 10, 15)), '10:42 am');
    expect(friendlyWhen('2026-11-09T21:05:00', now: DateTime(2026, 11, 10, 15)), 'Yesterday 9:05 pm');
  });

  test('today\'s numbers come from the footfall summary', () async {
    final (repo, _, f) = await signedIn();
    final t = await repo.today();
    expect(t.peopleIn, 120);
    expect(t.insideNow, 20);
    expect(t.peakHour, '5 pm');
    expect(f.calls, contains('GET /v1/footfall/summary'));
  });

  test('cameras show their recorder and leave out paused ones', () async {
    final (repo, _, _) = await signedIn();
    final cams = await repo.cameras();
    expect(cams.map((c) => c.name), ['Front door']);
    expect(cams.single.recorder, 'Store recorder');
    expect(cams.single.working, isTrue);
  });

  test('area alerts take their numbers from the area settings, without the repeat time', () async {
    final (repo, _, _) = await signedIn();
    final defs = await repo.alertDefs();
    final q = defs.firstWhere((d) => d.type == 'queue_long');
    expect(q.area, isTrue);
    expect(q.smart, isTrue);
    expect(q.settings.map((s) => s.key), ['length', 'minutes', 'picture_minutes']);
    final pic = q.settings.last;
    expect((pic.min, pic.max, pic.def), (0, 60, 2), reason: 'the picture interval: 0 for none, 2 minutes unless chosen');
    expect(defs.first.settings, isEmpty);
  });

  test('the areas an alert is watching are listed, and changing one updates it in place', () async {
    final (repo, _, f) = await signedIn();
    final areas = await repo.areas();
    expect(areas.map((a) => (a.id, a.cameraId, a.kind)), [('z1', 'c1', 'area_count')]);
    expect(areas.single.params, {'limit': 5});
    final d = (await repo.alertDefs()).firstWhere((d) => d.type == 'queue_long');
    await repo.saveAlert(NewAlert(d, {'length': 7, 'minutes': 3, 'picture_minutes': 20}, ['p1'], cameraId: 'c1', cameraName: 'Front door', area: [[0.2, 0.2], [0.7, 0.2], [0.7, 0.7]], areaId: 'z1'));
    final sent = f.bodies['PUT /v1/cameras/c1/areas']['areas'] as List;
    expect(sent.length, 1, reason: 'the area was changed, not added a second time');
    expect(sent.single['id'], 'z1');
    expect(sent.single['params'], {'length': 7, 'minutes': 3, 'picture_minutes': 20});
    expect(sent.single['points'].first, [0.2, 0.2]);
  });

  test('saving an area alert keeps the camera\'s other areas and turns the alert on', () async {
    final (repo, _, f) = await signedIn();
    final q = (await repo.alertDefs()).firstWhere((d) => d.type == 'queue_long');
    await repo.saveAlert(NewAlert(q, {'length': 8, 'minutes': 4, 'picture_minutes': 5}, ['p1'], cameraId: 'c1', cameraName: 'Front door', area: [[0.1, 0.1], [0.6, 0.1], [0.6, 0.6]]));
    final areas = f.bodies['PUT /v1/cameras/c1/areas']['areas'] as List;
    expect(areas.length, 2);
    expect(areas.first['id'], 'z1');
    expect(areas.last['kind'], 'queue_area');
    expect(areas.last['params'], {'length': 8, 'minutes': 4, 'picture_minutes': 5});
    expect(areas.last['points'].length, 3);
    final rule = f.bodies['PUT /v1/alert-types/queue_long'];
    expect(rule['enabled'], isTrue);
    expect(rule['recipient_ids'], ['p1']);
  });

  test('snooze and live view call the endpoints the server has', () async {
    final (repo, _, f) = await signedIn();
    await repo.snooze('x1', minutes: 30);
    expect(f.bodies['POST /v1/alerts/x1/snooze'], {'minutes': 30});
    final s = await repo.startLive('c1', LiveMode.ai);
    expect(s.id, 's1');
    expect(s.smooth, isTrue, reason: 'the server said it has smooth video');
    expect(s.ice, ['stun:stun.example.test:3478']);
    expect(f.bodies['POST /v1/cameras/c1/live'], {'mode': 'ai'});
  });

  test('a reload with several screens loading at once renews the sign-in only once', () async {
    SharedPreferences.setMockInitialValues({'gi_next_rt': 'r0'});
    final used = <String>{};
    var refreshes = 0;
    final client = MockClient((r) async {
      if (r.url.path == '/v1/auth/refresh') {
        refreshes++;
        final t = jsonDecode(r.body)['refresh_token'] as String;
        await Future<void>.delayed(const Duration(milliseconds: 20));
        if (!used.add(t)) return http.Response('{"error":{"code":"token_reused","message":"The session was ended for safety."}}', 401);   // a replayed token ends the session
        return http.Response(jsonEncode({'access_token': 'a$refreshes', 'refresh_token': 'r$refreshes'}), 200);
      }
      return http.Response(jsonEncode(r.headers['Authorization'] == 'Bearer a1' ? {'ok': true} : {'error': {'code': 'x', 'message': 'no'}}), r.headers['Authorization'] == 'Bearer a1' ? 200 : 401);
    });
    final api = ApiClient(base: 'https://x.test', client: client, tokens: PrefsTokenStore(await SharedPreferences.getInstance()));
    await api.restore();
    expect(api.signedIn, true);
    final out = await Future.wait([api.call('GET', '/v1/me'), api.call('GET', '/v1/health-summary'), api.call('GET', '/v1/cameras'), api.call('GET', '/v1/push')]);
    expect(out.every((x) => x is Map && x['ok'] == true), true);
    expect(refreshes, 1, reason: 'one renewal shared by every request');
    expect(api.signedIn, true);
  });
}
