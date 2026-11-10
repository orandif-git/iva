import 'dart:typed_data';

import 'api_client.dart';
import 'live/live_rules.dart';
import 'models.dart';

export 'api_client.dart' show ApiError;

class LiveSession {
  final String id;
  final LiveMode mode;
  const LiveSession(this.id, this.mode);
}

/// Everything the screens need from the server. [ApiRepository] talks to the real backend; [MockRepository] runs the same screens with demo data.
abstract class Repository {
  Future<Health> health();
  Future<List<AlertItem>> alerts();
  Future<Footfall> today();
  Future<void> done(String alertId);
  Future<void> snooze(String alertId, {int minutes = 60});
  Future<List<CameraItem>> cameras();
  Future<List<AlertDef>> alertDefs();
  Future<List<Person>> people();
  Future<Uint8List?> snapshot(String cameraId);
  Future<void> saveAlert(NewAlert a);
  Future<void> setEnabled(AlertDef d, bool on);
  Future<void> sendTest(String alertType, String personId);
  Future<List<ReportItem>> reports();
  Future<LiveSession> startLive(String cameraId, LiveMode mode);
  Future<void> extendLive(LiveSession s);
  Future<void> stopLive(LiveSession s);

  /// The current picture, or null when the demo should draw its own.
  Future<Uint8List?> frame(LiveSession s);
  Future<void> signOut();
}

String _pretty(String s) => s.isEmpty ? s : s[0].toUpperCase() + s.substring(1).replaceAll('_', ' ');

const _areaKinds = {'people_in_area': 'area_count', 'queue_long': 'queue_area', 'phone_use': 'staff_area'};

class ApiRepository implements Repository {
  ApiRepository(this.api);
  final ApiClient api;
  Map<String, bool>? _smart;
  Map<String, String>? _recorders;

  @override
  Future<Health> health() async => Health.fromJson(await api.call('GET', '/v1/health-summary'));

  Future<Map<String, bool>> _families() async {
    if (_smart != null) return _smart!;
    final list = await api.call('GET', '/v1/alert-types') as List;
    return _smart = {for (final j in list) j['type'] as String: j['ai'] == true};
  }

  @override
  Future<List<AlertItem>> alerts() async {
    final fam = await _families();
    final list = await api.call('GET', '/v1/alerts', query: {'limit': '50'}) as List;
    return [
      for (final j in list)
        AlertItem.fromJson(j as Map<String, dynamic>)..smart = fam[j['rule_type']] ?? false
    ];
  }

  @override
  Future<Footfall> today() async {
    final n = DateTime.now();
    final from = DateTime(n.year, n.month, n.day);
    return Footfall.fromJson(await api.call('GET', '/v1/footfall/summary', query: {'from': from.toUtc().toIso8601String(), 'to': n.add(const Duration(hours: 1)).toUtc().toIso8601String()}) as Map<String, dynamic>);
  }

  @override
  Future<void> done(String id) => api.call('POST', '/v1/alerts/$id/acknowledge');

  @override
  Future<void> snooze(String id, {int minutes = 60}) => api.call('POST', '/v1/alerts/$id/snooze', body: {'minutes': minutes});

  @override
  Future<List<CameraItem>> cameras() async {
    _recorders ??= {for (final r in await api.call('GET', '/v1/recorders') as List) r['id'] as String: ((r['name'] ?? r['device_id']) as String)};
    final list = await api.call('GET', '/v1/cameras') as List;
    return [for (final j in list) if (j['enabled'] == true) CameraItem(j['id'], j['name'], _recorders![j['recorder_id']] ?? '', j['stream_state'] == 'streaming')];
  }

  @override
  Future<List<AlertDef>> alertDefs() async {
    final types = await api.call('GET', '/v1/alert-types') as List;
    final kinds = {for (final k in await api.call('GET', '/v1/area-kinds') as List) k['alert'] as String: k};
    return [
      for (final j in types)
        () {
          final type = j['type'] as String;
          final area = _areaKinds.containsKey(type);
          final raw = (area ? (kinds[type]?['settings'] ?? {}) : (j['settings'] ?? {})) as Map;
          final settings = <Setting>[
            for (final e in raw.entries)
              if (e.key != 'repeat_minutes' && e.value['type'] != 'boolean')
                Setting(e.key as String, (settingWords[e.key]?.$1) ?? e.key, (settingWords[e.key]?.$2) ?? '', ((e.value['min'] ?? 1) as num).toInt(), ((e.value['max'] ?? 60) as num).toInt(), ((e.value['default'] ?? e.value['min'] ?? 1) as num).toInt())
          ];
          return AlertDef(
              type: type,
              title: j['title'],
              description: j['description'],
              smart: j['ai'] == true,
              area: area,
              areaKind: _areaKinds[type],
              settings: settings,
              enabled: j['enabled'] == true,
              params: {for (final e in ((j['params'] ?? {}) as Map).entries) if (e.value is num) e.key as String: e.value as num},
              recipientIds: [for (final r in (j['recipient_ids'] as List)) r as String]);
        }()
    ];
  }

  @override
  Future<List<Person>> people() async => [
        for (final j in await api.call('GET', '/v1/recipients') as List)
          if (j['active'] == true) Person(j['id'], j['name'], (j['phone_masked'] ?? '') as String)
      ];

  @override
  Future<Uint8List?> snapshot(String cameraId) async {
    try {
      final r = await api.raw('GET', '/v1/cameras/$cameraId/snapshot');
      return r.statusCode == 200 ? r.bodyBytes : null;
    } catch (_) {
      return null;
    }
  }

  @override
  Future<void> saveAlert(NewAlert a) async {
    if (a.def.area && a.cameraId != null && a.area != null) {
      final existing = await api.call('GET', '/v1/cameras/${a.cameraId}/areas') as List;
      final params = {for (final e in a.params.entries) e.key: e.value, if (a.def.areaKind == 'staff_area') 'notice_confirmed': true};
      final areas = [
        for (final z in existing) {'id': z['id'], 'kind': z['kind'], 'name': z['name'], 'points': z['points'], 'params': {...(z['params'] as Map), if (z['kind'] == 'staff_area') 'notice_confirmed': true}},
        {'kind': a.def.areaKind, 'name': '${a.def.title} · ${a.cameraName ?? 'camera'}', 'points': a.area, 'params': params},
      ];
      await api.call('PUT', '/v1/cameras/${a.cameraId}/areas', body: {'areas': areas});
    }
    await api.call('PUT', '/v1/alert-types/${a.def.type}', body: {'enabled': true, 'params': a.def.area ? <String, num>{} : a.params, 'recipient_ids': a.recipientIds});
  }

  @override
  Future<void> setEnabled(AlertDef d, bool on) async {
    await api.call('PUT', '/v1/alert-types/${d.type}', body: {'enabled': on, 'params': d.area ? <String, num>{} : d.params, 'recipient_ids': d.recipientIds});
    d.enabled = on;
  }

  @override
  Future<void> sendTest(String type, String personId) => api.call('POST', '/v1/alert-types/$type/test', body: {'recipient_id': personId});

  @override
  Future<List<ReportItem>> reports() async {
    final list = await api.call('GET', '/v1/report-schedules') as List;
    return [for (final j in list) ReportItem(_pretty((j['report_type'] ?? 'report') as String), _pretty((j['frequency'] ?? '') as String), j['enabled'] != false)];
  }

  @override
  Future<LiveSession> startLive(String cameraId, LiveMode mode) async {
    final j = await api.call('POST', '/v1/cameras/$cameraId/live', body: {'mode': mode.name});
    return LiveSession(j['id'], mode);
  }

  @override
  Future<void> extendLive(LiveSession s) => api.call('POST', '/v1/live/${s.id}/extend');
  @override
  Future<void> stopLive(LiveSession s) => api.call('DELETE', '/v1/live/${s.id}');

  @override
  Future<Uint8List?> frame(LiveSession s) async {
    final r = await api.raw('GET', '/v1/live/${s.id}/frame');
    if (r.statusCode == 200) return r.bodyBytes;
    if (r.statusCode == 404) return null; // the picture is not ready yet
    if (r.statusCode == 410) throw ApiError(410, 'This view has closed.');
    return null;
  }

  @override
  Future<void> signOut() => api.signOut();
}

/// Demo data, like the clickable prototype. [problems] switches on a camera that is offline and a recorder that keeps dropping.
class MockRepository implements Repository {
  MockRepository({this.problems = false});
  bool problems;
  final Map<String, AlertState> _state = {};
  final List<AlertDef> _defs = [
    AlertDef(type: 'camera_offline', title: 'Camera offline', description: 'A camera stops sending pictures.', smart: false, enabled: true),
    AlertDef(type: 'recorder_offline', title: 'Recorder offline', description: 'The recorder loses its link.', smart: false, enabled: true),
    AlertDef(type: 'connection_unstable', title: 'Connection keeps dropping', description: 'The link goes up and down.', smart: false, enabled: true, settings: const [Setting('drops', 'Tell me after this many drops in an hour', 'drops', 2, 20, 5)], params: {'drops': 5}),
    AlertDef(type: 'people_in_area', title: 'Too many people in an area', description: 'Warns when a marked area is crowded.', smart: true, area: true, areaKind: 'area_count', settings: const [Setting('limit', 'Alert when more than', 'people', 1, 60, 10), Setting('minutes', 'for at least', 'minutes', 1, 60, 10)]),
    AlertDef(type: 'queue_long', title: 'Long queue', description: 'People wait too long at a counter.', smart: true, area: true, areaKind: 'queue_area', enabled: true, settings: const [Setting('length', 'Alert when more than', 'people waiting', 1, 40, 6), Setting('minutes', 'for at least', 'minutes', 1, 60, 5)], params: {'length': 6, 'minutes': 5}),
    AlertDef(type: 'phone_use', title: 'Phone in use at the counter', description: 'Possible phone use in a marked area.', smart: true, area: true, areaKind: 'staff_area', settings: const [Setting('seconds', 'Alert after', 'seconds', 10, 120, 30)]),
  ];

  @override
  Future<Health> health() async => Health(
        ok: !problems,
        issues: problems ? const [Issue('camera_down', 'critical', 'Stockroom camera is not sending pictures'), Issue('recorder_unstable', 'warning', 'Godown recorder keeps dropping (6 times in the last hour)')] : const [],
        recordersOnline: 2,
        recordersTotal: 2,
        camerasWorking: problems ? 7 : 8,
        camerasTotal: 8,
        openAlerts: (await alerts()).where((a) => a.state == AlertState.open).length,
        sent24h: 12,
        failed24h: 0,
        dataSecondsAgo: problems ? 190 : 8,
      );

  @override
  Future<List<AlertItem>> alerts() async {
    final all = [
      if (problems) AlertItem(id: 'p1', title: 'Stockroom camera is offline', body: 'No picture for 14 minutes. Check the power and the network cable.', rule: 'camera_offline', where: 'Stockroom', when: '14 min ago'),
      if (problems) AlertItem(id: 'p2', title: 'Godown recorder keeps dropping', body: 'The link went up and down 6 times. This is usually the internet at the godown.', rule: 'connection_unstable', where: 'Godown', when: '1 hour'),
      AlertItem(id: 'a1', title: '9 people waiting for 6 minutes', body: 'More than 6 people have waited over 5 minutes. A second counter may help.', rule: 'queue_long', where: 'Billing queue', when: '10:42 am', smart: true),
      AlertItem(id: 'a2', title: 'Possible phone use at the counter', body: 'A phone may have been in use in the marked area for over 30 seconds.', rule: 'phone_use', where: 'Cash counter', when: '9:15 am', smart: true),
    ];
    for (final a in all) {
      a.state = _state[a.id] ?? AlertState.open;
    }
    return all;
  }

  @override
  Future<Footfall> today() async => const Footfall(342, 324, '5 pm');
  @override
  Future<void> done(String id) async => _state[id] = AlertState.done;
  @override
  Future<void> snooze(String id, {int minutes = 60}) async => _state[id] = AlertState.snoozed;

  @override
  Future<List<CameraItem>> cameras() async => [
        const CameraItem('c1', 'Front door', 'Store recorder', true),
        const CameraItem('c2', 'Cash counter', 'Store recorder', true),
        const CameraItem('c3', 'Aisle 1', 'Store recorder', true),
        const CameraItem('c4', 'Aisle 2', 'Store recorder', true),
        const CameraItem('c5', 'Back door', 'Store recorder', true),
        const CameraItem('c6', 'Parking', 'Store recorder', true),
        CameraItem('c7', 'Stockroom', 'Godown recorder', !problems),
        const CameraItem('c8', 'Billing queue', 'Godown recorder', true),
      ];

  @override
  Future<List<AlertDef>> alertDefs() async => _defs;
  @override
  Future<List<Person>> people() async => const [Person('p1', 'Priya (you)', '+91 98•••• 3210'), Person('p2', 'Ravi, store manager', '+91 97•••• 5521'), Person('p3', 'Store phone', '+91 90•••• 0044')];
  @override
  Future<Uint8List?> snapshot(String cameraId) async => null;
  @override
  Future<void> saveAlert(NewAlert a) async {
    a.def.enabled = true;
    a.def.params = a.params;
    a.def.recipientIds = a.recipientIds;
  }

  @override
  Future<void> setEnabled(AlertDef d, bool on) async => d.enabled = on;
  @override
  Future<void> sendTest(String alertType, String personId) async {}
  @override
  Future<List<ReportItem>> reports() async => const [ReportItem('Daily summary', 'Every day, 9 pm', true), ReportItem('Weekly footfall', 'Mondays, 8 am', true), ReportItem('Month end', 'Last day of month', false)];
  @override
  Future<LiveSession> startLive(String cameraId, LiveMode mode) async => LiveSession('demo-$cameraId', mode);
  @override
  Future<void> extendLive(LiveSession s) async {}
  @override
  Future<void> stopLive(LiveSession s) async {}
  @override
  Future<Uint8List?> frame(LiveSession s) async => null;
  @override
  Future<void> signOut() async {}
}
