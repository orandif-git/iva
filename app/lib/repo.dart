import 'dart:typed_data';

import 'api_client.dart';
import 'live/live_rules.dart';
import 'live/jpeg_splitter.dart';
import 'geometry/polygon.dart';
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
  Future<Me> me();
  Future<List<AlertItem>> alerts();
  Future<Footfall> today();
  Future<FootfallRange> footfallRange({required Period period, String? cameraId});
  Future<PlatePage> plates({String q = '', String? cursor, Period period = Period.week, String? cameraId});
  Future<List<AlertCount>> alertCounts(Period period);
  Future<Uint8List?> alertImage(String alertId);
  Future<EvidenceLink> evidenceLink(String alertId);
  Future<void> renameCamera(String cameraId, String name);
  Future<Uint8List?> plateImage(String id);
  Future<List<BlockedPlate>> blockedPlates();
  Future<void> blockPlate(String plate, {String? note});
  Future<void> unblockPlate(String id);
  Future<CameraSettings> cameraSettings(String id);
  Future<void> saveZones(String cameraId, List<CameraZone> zones);
  Future<void> setStream(String cameraId, String stream);
  Future<void> setCameraEnabled(String cameraId, bool enabled);
  Future<void> removeCamera(String cameraId);
  Future<void> done(String alertId);
  Future<void> snooze(String alertId, {int minutes = 60});
  Future<List<CameraItem>> cameras();
  Future<List<RecorderItem>> recorders();
  Future<RecorderSetup> addRecorder(String? name);
  Future<RecorderSetup> recorderConnection(String id);
  Future<RecorderCheck> recorderStatus(String id);
  Future<List<Channel>> channels(String id);
  Future<void> addCamera(String recorderId, int channelNo, String name, {bool people = true, bool plates = false});
  Future<void> renameRecorder(String id, String name);
  Future<void> removeRecorder(String id);
  Future<RecorderSetup> newRecorderKey(String id);
  Future<void> addPerson(String name, String phone, {String? email});
  Future<void> deletePerson(String id);
  Future<List<AlertDef>> alertDefs();
  Future<List<Person>> people();
  Future<Uint8List?> snapshot(String cameraId);
  Future<void> saveAlert(NewAlert a);
  Future<void> setEnabled(AlertDef d, bool on);
  Future<void> sendTest(String alertType, String personId);
  Future<List<ReportSchedule>> schedules();
  Future<void> saveSchedule(ReportSchedule s);
  Future<void> deleteSchedule(String id);
  Future<List<MadeReport>> madeReports();
  Future<void> makeReport(String type, DateTime from, DateTime to);
  Future<({String name, Uint8List bytes})> downloadReport(String id);
  Future<LiveSession> startLive(String cameraId, LiveMode mode);
  Future<void> extendLive(LiveSession s);
  Future<void> stopLive(LiveSession s);

  /// The current picture, or null when the demo should draw its own.
  Future<Uint8List?> frame(LiveSession s);
  /// Moving pictures for a live view, or null when the server cannot give them (the screen then refreshes a picture every second).
  Future<Stream<Uint8List>?> liveStream(LiveSession s);
  Future<bool> pushEnabled();
  Future<void> registerDevice(String token, String platform);
  Future<void> removeDevice(String token);
  Future<void> signOut();

  /// This device stays signed in. With [signOutOthers] every other device and browser is signed out and signs in again with the new password.
  Future<void> changePassword(String current, String next, {bool signOutOthers = true});
}

const _areaKinds = {'people_in_area': 'area_count', 'queue_long': 'queue_area', 'phone_use': 'staff_area'};

class ApiRepository implements Repository {
  ApiRepository(this.api);
  final ApiClient api;
  Map<String, bool>? _smart;
  Map<String, String>? _recorders;

  @override
  Future<Health> health() async => Health.fromJson(await api.call('GET', '/v1/health-summary'));

  @override
  Future<Me> me() async => Me.fromJson(await api.call('GET', '/v1/me') as Map<String, dynamic>);

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

  /// Midnight in India, as the API wants it (days are cut there).
  static String _istDay(int offsetDays) {
    final t = DateTime.now().toUtc().add(Duration(hours: 5, minutes: 30, days: offsetDays));
    return '${t.year.toString().padLeft(4, '0')}-${t.month.toString().padLeft(2, '0')}-${t.day.toString().padLeft(2, '0')}T00:00:00+05:30';
  }

  @override
  Future<FootfallRange> footfallRange({required Period period, String? cameraId}) async {
    final from = _istDay(period.fromDay), to = _istDay(period.toDay), hourly = period.hourly;
    final q = {'from': from, 'to': to, 'tz': 'Asia/Kolkata', if (cameraId != null) 'camera_id': cameraId};
    final r = await Future.wait([api.call('GET', '/v1/footfall', query: {...q, 'interval': hourly ? 'hour' : 'day'}), api.call('GET', '/v1/footfall/summary', query: q)]);
    final f = r[0] as Map, sum = r[1] as Map;
    String label(String iso) {
      final t = DateTime.parse(iso).toUtc().add(const Duration(hours: 5, minutes: 30));
      if (hourly) return '${t.hour % 12 == 0 ? 12 : t.hour % 12}${t.hour < 12 ? 'am' : 'pm'}';
      const m = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
      return '${t.day} ${m[t.month - 1]}';
    }
    final peak = sum['peak_hour'] as Map?;
    return FootfallRange((sum['people_in'] as num).toInt(), (sum['people_out'] as num).toInt(), peak == null ? null : label(peak['bucket'] as String),
        [for (final p in f['items'] as List) FootfallBar(label(p['bucket'] as String), (p['people_in'] as num).toInt(), (p['people_out'] as num).toInt())]);
  }

  @override
  Future<PlatePage> plates({String q = '', String? cursor, Period period = Period.week, String? cameraId}) async {
    final r = await api.call('GET', '/v1/plates', query: {if (cameraId != null) 'camera_id': cameraId, 'limit': '30', 'from': _istDay(period.fromDay), 'to': _istDay(period.toDay), if (q.trim().isNotEmpty) 'q': q.trim(), if (cursor != null) 'cursor': cursor}) as Map;
    return PlatePage([
      for (final j in r['items'] as List)
        PlateRead('${j['id']}', '${j['camera_id']}', j['plate_text'] as String?, DateTime.tryParse('${j['seen_at']}')?.toLocal(),
            vehicle: j['vehicle_type'] as String?, direction: j['direction'] as String?, blocked: j['blocked'] == true, needsReview: j['review_status'] == 'needs_review',
            hasPicture: j['has_snapshot'] == true, confidence: (j['confidence'] as num?)?.toInt(), dwellMinutes: j['dwell_seconds'] == null ? null : ((j['dwell_seconds'] as num) / 60).round())
    ], r['next_cursor'] as String?);
  }

  @override
  Future<Uint8List?> alertImage(String alertId) async {
    final r = await api.raw('GET', '/v1/alerts/$alertId/image');
    return r.statusCode == 200 ? r.bodyBytes : null;
  }

  @override
  Future<EvidenceLink> evidenceLink(String alertId) async {
    final j = await api.call('POST', '/v1/alerts/$alertId/evidence-link') as Map;
    return EvidenceLink(j['url'] as String, hasImage: j['has_image'] == true, hasClip: j['has_clip'] == true);
  }

  @override
  Future<List<AlertCount>> alertCounts(Period period) async => [
        for (final j in await api.call('GET', '/v1/alerts/summary', query: {'from': _istDay(period.fromDay), 'to': _istDay(period.toDay)}) as List)
          AlertCount(j['rule_type'] as String, (j['place'] ?? '') as String, (j['count'] as num).toInt(), DateTime.tryParse('${j['last_at']}')?.toLocal())
      ];

  @override
  Future<void> renameCamera(String cameraId, String name) async {
    await api.call('PATCH', '/v1/cameras/$cameraId', body: {'name': name.trim()});
    _recorders = null;
  }

  @override
  Future<List<BlockedPlate>> blockedPlates() async => [
        for (final j in await api.call('GET', '/v1/vehicle-lists', query: {'list_type': 'blocked'}) as List)
          BlockedPlate('${j['id']}', j['plate_text'] as String, note: j['note'] as String?, added: DateTime.tryParse('${j['created_at']}')?.toLocal())
      ];

  @override
  Future<void> blockPlate(String plate, {String? note}) =>
      api.call('POST', '/v1/vehicle-lists', body: {'plate_text': plate.trim(), 'list_type': 'blocked', if (note != null && note.trim().isNotEmpty) 'note': note.trim()});

  @override
  Future<void> unblockPlate(String id) => api.call('DELETE', '/v1/vehicle-lists/$id');

  @override
  Future<Uint8List?> plateImage(String id) async {
    final r = await api.raw('GET', '/v1/plates/$id/image');
    return r.statusCode == 200 ? r.bodyBytes : null;
  }

  @override
  Future<CameraSettings> cameraSettings(String id) async {
    final r = await Future.wait([api.call('GET', '/v1/cameras'), api.call('GET', '/v1/cameras/$id/zones')]);
    final cam = (r[0] as List).cast<Map>().firstWhere((c) => c['id'] == id, orElse: () => throw ApiError(404, 'That camera was not found.'));
    List<Pt> pts(Object? l) => [for (final p in (l as List? ?? const [])) Pt((p[0] as num).toDouble(), (p[1] as num).toDouble())];
    return CameraSettings(id, cam['name'] as String, useFootfall: cam['use_footfall'] == true, useAnpr: cam['use_anpr'] == true, enabled: cam['enabled'] == true, stream: (cam['stream'] ?? 'sub') as String, zones: [
      for (final z in (r[1] as List).cast<Map>())
        CameraZone(z['kind'] as String, pts(z['points']), inDirection: z['in_direction'] == null ? null : Pt((z['in_direction'][0] as num).toDouble(), (z['in_direction'][1] as num).toDouble()))
    ]);
  }

  @override
  Future<void> saveZones(String cameraId, List<CameraZone> zones) => api.call('PUT', '/v1/cameras/$cameraId/zones', body: {
        'zones': [for (final z in zones) {'kind': z.kind, 'points': [for (final p in z.points) p.toJson()], if (z.inDirection != null) 'in_direction': z.inDirection!.toJson()}]
      });

  @override
  Future<void> setStream(String cameraId, String stream) => api.call('PATCH', '/v1/cameras/$cameraId', body: {'stream': stream});

  @override
  Future<void> setCameraEnabled(String cameraId, bool enabled) async {
    await api.call('PATCH', '/v1/cameras/$cameraId', body: {'enabled': enabled});
    _recorders = null;
  }

  @override
  Future<void> removeCamera(String cameraId) async {
    await api.call('DELETE', '/v1/cameras/$cameraId');
    _recorders = null;
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
    int? ago(String? iso) {
      final d = iso == null ? null : DateTime.tryParse(iso);
      return d == null ? null : DateTime.now().toUtc().difference(d.toUtc()).inSeconds.clamp(0, 1 << 30);
    }
    return [for (final j in list) if (j['enabled'] == true) CameraItem(j['id'], j['name'], _recorders![j['recorder_id']] ?? '', j['stream_state'] == 'streaming', seenAgo: ago(j['last_frame_at'] as String?), useFootfall: j['use_footfall'] == true, useAnpr: j['use_anpr'] == true)];
  }

  @override
  Future<RecorderSetup> addRecorder(String? name) async => RecorderSetup.fromJson(await api.call('POST', '/v1/recorders', body: {'brand': 'dahua', if (name != null && name.trim().isNotEmpty) 'name': name.trim()}) as Map<String, dynamic>);
  @override
  Future<RecorderSetup> recorderConnection(String id) async => RecorderSetup.fromJson(await api.call('GET', '/v1/recorders/$id/connection') as Map<String, dynamic>);
  @override
  Future<RecorderCheck> recorderStatus(String id) async => RecorderCheck.fromJson(await api.call('GET', '/v1/recorders/$id/status') as Map<String, dynamic>);
  @override
  Future<List<Channel>> channels(String id) async => [for (final j in await api.call('GET', '/v1/recorders/$id/channels') as List) Channel((j['channel_no'] as num).toInt(), j['name'] as String?, j['active'] == true, j['enabled_as_camera'] == true)];
  @override
  Future<void> addCamera(String recorderId, int channelNo, String name, {bool people = true, bool plates = false}) async {
    await api.call('POST', '/v1/cameras', body: {'recorder_id': recorderId, 'channel_no': channelNo, 'name': name, 'use_footfall': people, 'use_anpr': plates});
    _recorders = null;
  }
  @override
  Future<void> renameRecorder(String id, String name) async {
    await api.call('PATCH', '/v1/recorders/$id', body: {'name': name.trim().isEmpty ? null : name.trim()});
    _recorders = null;
  }
  @override
  Future<void> removeRecorder(String id) async {
    await api.call('DELETE', '/v1/recorders/$id');
    _recorders = null;
  }
  @override
  Future<RecorderSetup> newRecorderKey(String id) async => RecorderSetup.fromJson(await api.call('POST', '/v1/recorders/$id/rotate-key') as Map<String, dynamic>);

  @override
  Future<List<RecorderItem>> recorders() async => [
        for (final r in await api.call('GET', '/v1/recorders') as List)
          RecorderItem(r['id'], ((r['name'] ?? r['device_id']) as String), (r['status'] ?? 'never') as String, (r['channel_count'] ?? 0) as int,
              seenAgo: r['last_heartbeat_at'] == null ? null : DateTime.now().toUtc().difference(DateTime.parse(r['last_heartbeat_at'] as String).toUtc()).inSeconds.clamp(0, 1 << 30))
      ];

  @override
  Future<void> addPerson(String name, String phone, {String? email}) =>
      api.call('POST', '/v1/recipients', body: {'name': name, 'phone': phone, if (email != null && email.isNotEmpty) 'email': email, 'consent_confirmed': true});

  @override
  Future<void> deletePerson(String id) => api.call('DELETE', '/v1/recipients/$id');

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
          if (j['active'] == true) Person(j['id'], j['name'], (j['phone_masked'] ?? '') as String, email: j['email'] as String?)
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
  Future<List<ReportSchedule>> schedules() async => [for (final j in await api.call('GET', '/v1/report-schedules') as List) ReportSchedule.fromJson(j as Map<String, dynamic>)];

  @override
  Future<void> saveSchedule(ReportSchedule s) => s.id == null ? api.call('POST', '/v1/report-schedules', body: s.toJson()) : api.call('PUT', '/v1/report-schedules/${s.id}', body: s.toJson());

  @override
  Future<void> deleteSchedule(String id) => api.call('DELETE', '/v1/report-schedules/$id');

  @override
  Future<List<MadeReport>> madeReports() async {
    final list = await api.call('GET', '/v1/reports', query: {'limit': '20'}) as List;
    String day(String? iso) => iso == null ? '' : friendlyWhen(iso).split(' ').take(3).join(' ');
    return [for (final j in list) MadeReport(j['id'], j['report_type'], j['status'] as String, '${day(j['period_start'] as String?)} – ${day(j['period_end'] as String?)}', j['rows'] as int?)];
  }

  @override
  Future<void> makeReport(String type, DateTime from, DateTime to) {
    String d(DateTime x) => '${x.year}-${x.month.toString().padLeft(2, '0')}-${x.day.toString().padLeft(2, '0')}';
    return api.call('POST', '/v1/reports', body: {'report_type': type, 'from': d(from), 'to': d(to)});
  }

  @override
  Future<({String name, Uint8List bytes})> downloadReport(String id) => api.file('/v1/reports/$id/download', fallbackName: 'report.xlsx');

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
  Future<Stream<Uint8List>?> liveStream(LiveSession s) async {
    try {
      final body = await api.stream('/v1/live/${s.id}/stream');
      final cut = JpegSplitter();
      return body.expand((chunk) => cut.add(chunk));
    } on ApiError catch (e) {
      if (e.status == 410) rethrow;
      return null;                    // busy, offline or not supported: pictures instead
    }
  }

  @override
  Future<bool> pushEnabled() async => ((await api.call('GET', '/v1/push')) as Map)['enabled'] == true;
  @override
  Future<void> registerDevice(String token, String platform) => api.call('POST', '/v1/devices', body: {'token': token, 'platform': platform});
  @override
  Future<void> removeDevice(String token) => api.call('POST', '/v1/devices/remove', body: {'token': token});

  @override
  Future<void> signOut() => api.signOut();

  @override
  Future<void> changePassword(String current, String next, {bool signOutOthers = true}) =>
      api.call('POST', '/v1/auth/change-password', body: {'current_password': current, 'new_password': next, 'sign_out_others': signOutOthers, if (api.refreshToken != null) 'refresh_token': api.refreshToken});
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
  Future<Me> me() async => const Me('Priya', 'Green Valley Store', 'admin', plan: 'Full', camerasInUse: 8, cameraLimit: 20);

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
      AlertItem(id: 'a1', title: '9 people waiting for 6 minutes', body: 'More than 6 people have waited over 5 minutes. A second counter may help.', rule: 'queue_long', where: 'Billing queue', when: '10:42 am', smart: true, hasEvidence: true, hasClip: true),
      AlertItem(id: 'a2', title: 'Possible phone use at the counter', body: 'A phone may have been in use in the marked area for over 30 seconds.', rule: 'phone_use', where: 'Cash counter', when: '9:15 am', smart: true, hasEvidence: true),
    ];
    for (final a in all) {
      a.state = _state[a.id] ?? AlertState.open;
    }
    return all;
  }

  @override
  Future<Footfall> today() async => const Footfall(342, 324, '5 pm');
  final Map<String, List<CameraZone>> zonesSaved = {};
  String? streamSet;
  bool? cameraEnabled;
  bool cameraRemoved = false;
  @override
  Future<FootfallRange> footfallRange({required Period period, String? cameraId}) async => period.hourly
      ? FootfallRange(period == Period.yesterday ? 410 : 342, period == Period.yesterday ? 402 : 324, '5pm', [for (var h = 9; h <= 20; h++) FootfallBar('${h % 12 == 0 ? 12 : h % 12}${h < 12 ? 'am' : 'pm'}', 10 + h * 2, 8 + h * 2)])
      : FootfallRange(period == Period.week ? 2310 : 9800, period == Period.week ? 2200 : 9500, '6pm', [for (var d = 0; d < (period == Period.week ? 7 : 10); d++) FootfallBar('${d + 1} Nov', 300 + d * 10, 290 + d * 9)]);
  final linksOpened = <String>[];
  @override
  Future<Uint8List?> alertImage(String alertId) async => alertId == 'a1' ? _tinyPicture : null;
  @override
  Future<EvidenceLink> evidenceLink(String alertId) async {
    if (alertId == 'gone') throw ApiError(404, 'There is nothing stored for this alert. It may have been deleted after the retention period.');
    linksOpened.add(alertId);
    return EvidenceLink('https://example.test/e/token-$alertId', hasImage: true, hasClip: alertId == 'a1');
  }
  final Map<String, List<AlertCount>> alertCountsByPeriod = {};
  @override
  Future<List<AlertCount>> alertCounts(Period period) async => alertCountsByPeriod[period.name] ?? [
        AlertCount('queue_long', 'Billing queue', period == Period.today ? 2 : 9, DateTime(2026, 11, 11, 10, 40)),
        AlertCount('people_in_area', 'Aisle 1', period == Period.today ? 5 : 23, DateTime(2026, 11, 11, 1, 31)),
        AlertCount('camera_offline', 'Stockroom', 1, DateTime(2026, 11, 11, 8, 5)),
        AlertCount('recorder_offline', 'Godown recorder', 2, DateTime(2026, 11, 10, 22, 15)),
      ];
  final renamedCameras = <String, String>{};
  @override
  Future<void> renameCamera(String cameraId, String name) async => renamedCameras[cameraId] = name.trim();
  @override
  Future<PlatePage> plates({String q = '', String? cursor, Period period = Period.week, String? cameraId}) async {
    final all = [
      PlateRead('p1', 'c1', 'GJ14AB1234', DateTime(2026, 11, 10, 9, 30), vehicle: 'car', direction: 'in', hasPicture: true, confidence: 97),
      PlateRead('p2', 'c1', 'GJ05CD9876', DateTime(2026, 11, 10, 9, 12), vehicle: 'truck', direction: 'out', dwellMinutes: 42),
      PlateRead('p3', 'c1', null, DateTime(2026, 11, 10, 8, 55), vehicle: 'bike', needsReview: true),
    ];
    PlateRead mark(PlateRead p) => PlateRead(p.id, p.cameraId, p.plate, p.seen, vehicle: p.vehicle, direction: p.direction, blocked: blocked.any((b) => b.plate == p.plate), needsReview: p.needsReview, hasPicture: p.hasPicture, confidence: p.confidence, dwellMinutes: p.dwellMinutes);
    final shown = [for (final p in all) mark(p)];
    return PlatePage(q.isEmpty ? shown : shown.where((p) => (p.plate ?? '').contains(q.toUpperCase())).toList(), null);
  }
  final blocked = <BlockedPlate>[BlockedPlate('b1', 'GJ05CD9876', note: 'Blocked the gate twice', added: DateTime(2026, 11, 5))];
  @override
  Future<List<BlockedPlate>> blockedPlates() async => List.of(blocked);
  @override
  Future<void> blockPlate(String plate, {String? note}) async {
    final p = plate.replaceAll(' ', '').toUpperCase();
    if (p.length < 8) throw ApiError(422, 'That is not a valid Indian plate, for example GJ14AW1013.', code: 'invalid_plate');
    if (blocked.any((b) => b.plate == p)) throw ApiError(409, 'This plate is already on that list.', code: 'already_listed');
    blocked.insert(0, BlockedPlate('b${blocked.length + 1}', p, note: note, added: DateTime(2026, 11, 11)));
  }
  @override
  Future<void> unblockPlate(String id) async => blocked.removeWhere((b) => b.id == id);
  @override
  Future<Uint8List?> plateImage(String id) async => null;
  @override
  Future<CameraSettings> cameraSettings(String id) async => CameraSettings(id, 'Front door', useFootfall: true, useAnpr: true, enabled: cameraEnabled ?? true, stream: streamSet ?? 'sub', zones: zonesSaved[id] ?? const []);
  @override
  Future<void> saveZones(String cameraId, List<CameraZone> zones) async => zonesSaved[cameraId] = zones;
  @override
  Future<void> setStream(String cameraId, String stream) async => streamSet = stream;
  @override
  Future<void> setCameraEnabled(String cameraId, bool enabled) async => cameraEnabled = enabled;
  @override
  Future<void> removeCamera(String cameraId) async => cameraRemoved = true;

  @override
  Future<void> done(String id) async => _state[id] = AlertState.done;
  @override
  Future<void> snooze(String id, {int minutes = 60}) async => _state[id] = AlertState.snoozed;

  @override
  Future<List<CameraItem>> cameras() async => [
        const CameraItem('c1', 'Front door', 'Store recorder', true, useFootfall: true),
        const CameraItem('c2', 'Cash counter', 'Store recorder', true, useFootfall: true),
        const CameraItem('c3', 'Aisle 1', 'Store recorder', true),
        const CameraItem('c4', 'Aisle 2', 'Store recorder', true),
        const CameraItem('c5', 'Back door', 'Store recorder', true),
        const CameraItem('c6', 'Parking', 'Store recorder', true, useAnpr: true),
        CameraItem('c7', 'Stockroom', 'Godown recorder', !problems),
        const CameraItem('c8', 'Billing queue', 'Godown recorder', true),
      ];

  int _polls = 0;
  final Set<int> _added = {};
  String? lastRenamed;
  bool removed = false;
  @override
  Future<RecorderSetup> addRecorder(String? name) async => const RecorderSetup('r9', 'connect.example.in', 7681, 'GI-AB12CD', 'giview', key: 'k3y-once-only');
  @override
  Future<RecorderSetup> recorderConnection(String id) async => const RecorderSetup('r1', 'connect.example.in', 7681, 'GI-AB12CD', 'giview');
  @override
  Future<RecorderCheck> recorderStatus(String id) async => id == 'r9' && ++_polls < 2 ? const RecorderCheck('never', {'reached': false}) : const RecorderCheck('online', {'reached': true, 'key_ok': true, 'channels_found': true, 'preview_ok': true});
  @override
  Future<List<Channel>> channels(String id) async => [Channel(1, null, true, _added.contains(1)), Channel(2, 'Back door', true, _added.contains(2))];
  @override
  Future<void> addCamera(String recorderId, int channelNo, String name, {bool people = true, bool plates = false}) async => _added.add(channelNo);
  @override
  Future<void> renameRecorder(String id, String name) async => lastRenamed = name;
  @override
  Future<void> removeRecorder(String id) async => removed = true;
  @override
  Future<RecorderSetup> newRecorderKey(String id) async => const RecorderSetup('r1', 'connect.example.in', 7681, 'GI-AB12CD', 'giview', key: 'brand-new-key');
  @override
  Future<List<RecorderItem>> recorders() async => [RecorderItem('r1', 'Store recorder', 'online', 6, seenAgo: 8), RecorderItem('r2', 'Godown recorder', problems ? 'offline' : 'online', 2, seenAgo: problems ? 7800 : 12)];
  final List<Person> _people = [const Person('p1', 'Priya (you)', '+91 98•••• 3210', email: 'priya@greenvalley.in'), const Person('p2', 'Ravi, store manager', '+91 97•••• 5521', email: 'ravi@greenvalley.in'), const Person('p3', 'Store phone', '+91 90•••• 0044')];
  @override
  Future<void> addPerson(String name, String phone, {String? email}) async => _people.add(Person('p${_people.length + 1}', name, phone, email: email));
  @override
  Future<void> deletePerson(String id) async => _people.removeWhere((p) => p.id == id);
  @override
  Future<List<AlertDef>> alertDefs() async => _defs;
  @override
  Future<List<Person>> people() async => List.of(_people);
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
  final List<ReportSchedule> _schedules = [
    ReportSchedule(id: 's1', type: 'footfall_summary', frequency: 'daily', sendAt: '21:00', recipientIds: ['p1', 'p2']),
    ReportSchedule(id: 's2', type: 'footfall_summary', frequency: 'weekly', weekday: 0, sendAt: '08:00', recipientIds: ['p1']),
    ReportSchedule(id: 's3', type: 'vehicle_audit', frequency: 'monthly', dayOfMonth: 1, sendAt: '07:00', recipientIds: ['p1'], enabled: false),
  ];
  final List<MadeReport> _made = [const MadeReport('m1', 'footfall_summary', 'ready', '3 Nov – 9 Nov', 1204), const MadeReport('m2', 'gate_register', 'ready', '1 Nov – 7 Nov', 86)];
  @override
  Future<List<ReportSchedule>> schedules() async => List.of(_schedules);
  @override
  Future<void> saveSchedule(ReportSchedule s) async {
    final i = _schedules.indexWhere((x) => x.id == s.id);
    if (i >= 0) {
      _schedules[i] = s;
    } else {
      _schedules.add(ReportSchedule(id: 's${_schedules.length + 1}', type: s.type, frequency: s.frequency, sendAt: s.sendAt, weekday: s.weekday, dayOfMonth: s.dayOfMonth, recipientIds: s.recipientIds, enabled: s.enabled));
    }
  }

  @override
  Future<void> deleteSchedule(String id) async => _schedules.removeWhere((s) => s.id == id);
  @override
  Future<List<MadeReport>> madeReports() async => List.of(_made);
  @override
  Future<void> makeReport(String type, DateTime from, DateTime to) async => _made.insert(0, MadeReport('m${_made.length + 1}', type, 'queued', 'Today', null));
  @override
  Future<({String name, Uint8List bytes})> downloadReport(String id) async => (name: 'Report $id.xlsx', bytes: Uint8List.fromList([80, 75, 3, 4]));
  @override
  Future<LiveSession> startLive(String cameraId, LiveMode mode) async => LiveSession('demo-$cameraId', mode);
  @override
  Future<void> extendLive(LiveSession s) async {}
  @override
  Future<void> stopLive(LiveSession s) async {}
  @override
  Future<Uint8List?> frame(LiveSession s) async => null;
  @override
  Future<Stream<Uint8List>?> liveStream(LiveSession s) async => null;
  bool push = false;
  final registered = <String>[];
  @override
  Future<bool> pushEnabled() async => push;
  @override
  Future<void> registerDevice(String token, String platform) async => registered.add('$token $platform');
  @override
  Future<void> removeDevice(String token) async => registered.remove(registered.firstWhere((x) => x.startsWith(token), orElse: () => ''));
  @override
  Future<void> signOut() async {}
  bool? lastSignOutOthers;
  @override
  Future<void> changePassword(String current, String next, {bool signOutOthers = true}) async => lastSignOutOthers = signOutOthers;
}

/// A one-pixel picture, so the demo and the tests have something to show where a camera would give a real one.
final _tinyPicture = Uint8List.fromList(const [
  0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,
  0x89, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9C, 0x63, 0x60, 0x60, 0xF8, 0xFF, 0x1F, 0x00, 0x03, 0x01, 0x01, 0x00, 0x35, 0x7B, 0x1F, 0xD6, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45,
  0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82
]);
