import 'geometry/polygon.dart';
import 'package:flutter/material.dart' show Icons;
class Issue {
  final String kind, level, title;
  const Issue(this.kind, this.level, this.title);
}

class Health {
  final bool ok;
  final List<Issue> issues;
  final int recordersOnline, recordersTotal, camerasWorking, camerasTotal, openAlerts, sent24h, failed24h;
  final int? dataSecondsAgo;
  const Health({required this.ok, required this.issues, required this.recordersOnline, required this.recordersTotal, required this.camerasWorking,
      required this.camerasTotal, required this.openAlerts, required this.sent24h, required this.failed24h, this.dataSecondsAgo});

  factory Health.fromJson(Map<String, dynamic> j) => Health(
        ok: j['status'] == 'ok',
        issues: [for (final i in (j['issues'] as List)) Issue(i['kind'], i['level'], i['title'])],
        recordersOnline: j['recorders']['online'],
        recordersTotal: j['recorders']['total'],
        camerasWorking: j['cameras']['working'],
        camerasTotal: j['cameras']['total'],
        openAlerts: j['open_alerts'],
        sent24h: j['messages']['sent_24h'],
        failed24h: j['messages']['failed_24h'],
        dataSecondsAgo: j['data']['newest_seconds_ago'],
      );
}

enum AlertState { open, snoozed, done }

class AlertItem {
  final String id, title, body, rule, where, when;
  final DateTime? at;
  bool smart;
  AlertState state;
  AlertItem({required this.id, required this.title, required this.body, required this.rule, required this.where, required this.when, this.at, this.smart = false, this.state = AlertState.open});

  factory AlertItem.fromJson(Map<String, dynamic> j) => AlertItem(
        id: j['id'],
        title: j['title'],
        body: j['body'] ?? '',
        rule: j['rule_type'] ?? '',
        where: j['camera_name'] ?? '',
        when: friendlyWhen((j['created_at'] ?? '') as String),
        at: DateTime.tryParse((j['created_at'] ?? '') as String)?.toLocal(),
        state: AlertState.values.firstWhere((s) => s.name == j['state'], orElse: () => AlertState.open),
      );
}

class CameraItem {
  final String id, name, recorder;
  final bool working;
  final int? seenAgo; // seconds since its last picture, when known
  const CameraItem(this.id, this.name, this.recorder, this.working, {this.seenAgo});
}

class RecorderItem {
  final String id, name, status; // status: online, offline, never
  final int channels;
  final int? seenAgo;
  const RecorderItem(this.id, this.name, this.status, this.channels, {this.seenAgo});
  bool get online => status == 'online';
}

/// What to type into the recorder. The password (key) is shown once, when it is made.
class RecorderSetup {
  final String id, server, deviceId, viewerUser;
  final int port;
  final String? key;
  const RecorderSetup(this.id, this.server, this.port, this.deviceId, this.viewerUser, {this.key});
  factory RecorderSetup.fromJson(Map<String, dynamic> j) => RecorderSetup((j['recorder_id'] ?? j['id']) as String, j['server'] as String, (j['port'] as num).toInt(), j['device_id'] as String, (j['viewer_user'] ?? 'giview') as String, key: j['key'] as String?);
}

/// Has the recorder reached us yet: the four ticks on the connect screen.
class RecorderCheck {
  final String status;
  final Map<String, bool> checks;
  const RecorderCheck(this.status, this.checks);
  bool get reached => checks['reached'] == true;
  bool get channelsFound => checks['channels_found'] == true;
  bool get done => ['reached', 'key_ok', 'channels_found', 'preview_ok'].every((k) => checks[k] == true);
  factory RecorderCheck.fromJson(Map<String, dynamic> j) => RecorderCheck(j['status'] as String, {for (final e in (j['checks'] as Map).entries) e.key as String: e.value == true});
}

/// A channel on the recorder: it becomes a camera when the customer adds it.
class Channel {
  final int no;
  final String? name;
  final bool active, enabled;
  const Channel(this.no, this.name, this.active, this.enabled);
  String get label => (name ?? '').isEmpty ? 'Camera $no' : name!;
}

/// One number the customer can set on an alert.
class Setting {
  final String key, label, unit;
  final int min, max, def;
  const Setting(this.key, this.label, this.unit, this.min, this.max, this.def);
}

/// An alert the customer can have: its name, what it does, the numbers to set, and whether it needs an area drawn on a camera.
class AlertDef {
  final String type, title, description;
  final bool smart, area;
  final String? areaKind; // area_count, queue_area, staff_area
  final List<Setting> settings;
  bool enabled;
  Map<String, num> params;
  List<String> recipientIds;
  AlertDef({required this.type, required this.title, required this.description, required this.smart, this.area = false, this.areaKind, this.settings = const [], this.enabled = false, this.params = const {}, this.recipientIds = const []});

  /// One plain sentence from the numbers: "Alert when more than 6 people waiting, for at least 5 minutes".
  String get summary {
    if (!enabled && params.isEmpty) return description;
    final bits = <String>[];
    for (final st in settings) {
      final v = params[st.key];
      if (v == null || (st.key == 'wait_minutes' && v == 0)) continue;
      final n = v == v.roundToDouble() ? '${v.toInt()}' : '$v';
      final text = st.key == 'hour' ? '${st.label} ${_clock(v)}' : [st.label, n, st.unit].where((x) => x.isNotEmpty).join(' ');
      bits.add(bits.isEmpty ? text : text[0].toLowerCase() + text.substring(1));
    }
    return bits.isEmpty ? description : bits.join(', ');
  }
}

class Person {
  final String id, name, phone;
  final String? email;
  const Person(this.id, this.name, this.phone, {this.email});
  bool get hasEmail => (email ?? '').isNotEmpty;
  bool get hasPhone => phone.isNotEmpty;
}

const reportTypes = {
  'footfall_summary': ('Footfall summary', 'People in and out by hour and day', Icons.groups_outlined),
  'gate_register': ('Gate register', 'Every vehicle seen at your gates', Icons.sensor_door_outlined),
  'vehicle_audit': ('Vehicle audit', 'Plates to review, blocked and allowed vehicles', Icons.directions_car_outlined),
};

/// An emailed report on a schedule.
class ReportSchedule {
  final String? id;
  final String type, frequency, sendAt; // frequency: daily, weekly, monthly; sendAt: "21:00"
  final int? weekday, dayOfMonth; // weekday 0 = Monday
  final List<String> recipientIds;
  bool enabled;
  ReportSchedule({this.id, required this.type, required this.frequency, this.sendAt = '07:00', this.weekday, this.dayOfMonth, this.recipientIds = const [], this.enabled = true});

  String get title => reportTypes[type]?.$1 ?? type;

  /// "Every day at 9 pm", "Mondays at 8 am", "On the 1st at 7 am".
  String get when {
    final h = int.tryParse(sendAt.split(':').first) ?? 7, m = sendAt.split(':').length > 1 ? sendAt.split(':')[1] : '00';
    final clock = '${h % 12 == 0 ? 12 : h % 12}${m == '00' ? '' : ':$m'} ${h < 12 ? 'am' : 'pm'}';
    const days = ['Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays', 'Sundays'];
    return switch (frequency) {
      'weekly' => '${days[(weekday ?? 0).clamp(0, 6)]} at $clock',
      'monthly' => 'On the ${_ordinal(dayOfMonth ?? 1)} at $clock',
      _ => 'Every day at $clock',
    };
  }

  static String _ordinal(int n) => '$n${n % 100 >= 11 && n % 100 <= 13 ? 'th' : (const ['th', 'st', 'nd', 'rd', 'th', 'th', 'th', 'th', 'th', 'th'][n % 10])}';

  factory ReportSchedule.fromJson(Map<String, dynamic> j) => ReportSchedule(
      id: j['id'], type: j['report_type'], frequency: j['frequency'], sendAt: j['send_at'] ?? '07:00', weekday: j['weekday'], dayOfMonth: j['day_of_month'], recipientIds: [for (final r in (j['recipient_ids'] as List? ?? [])) r as String], enabled: j['enabled'] != false);

  Map<String, dynamic> toJson() => {'report_type': type, 'frequency': frequency, 'send_at': sendAt, if (frequency == 'weekly') 'weekday': weekday ?? 0, if (frequency == 'monthly') 'day_of_month': dayOfMonth ?? 1, 'recipient_ids': recipientIds, 'enabled': enabled};
}

/// A report made now, for a period, as an Excel file.
class MadeReport {
  final String id, type, status, period;
  final int? rows;
  const MadeReport(this.id, this.type, this.status, this.period, this.rows);
  String get title => reportTypes[type]?.$1 ?? type;
  bool get ready => status == 'ready' || status == 'done' || status == 'completed';
  bool get failed => status == 'failed';
}

/// What to save when the New alert flow ends.
class NewAlert {
  final AlertDef def;
  final Map<String, num> params;
  final List<String> recipientIds;
  final String? cameraId, cameraName;
  final List<List<double>>? area;
  const NewAlert(this.def, this.params, this.recipientIds, {this.cameraId, this.cameraName, this.area});
}

/// "10:42 am" for today, "Yesterday" or "12 Nov" otherwise.
String friendlyWhen(String iso, {DateTime? now}) {
  final t = DateTime.tryParse(iso)?.toLocal();
  if (t == null) return iso;
  final n = now ?? DateTime.now();
  final d0 = DateTime(n.year, n.month, n.day), d1 = DateTime(t.year, t.month, t.day);
  final days = d0.difference(d1).inDays;
  final h = t.hour % 12 == 0 ? 12 : t.hour % 12;
  final clock = '$h:${t.minute.toString().padLeft(2, '0')} ${t.hour < 12 ? 'am' : 'pm'}';
  if (days == 0) return clock;
  if (days == 1) return 'Yesterday $clock';
  const m = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return '${t.day} ${m[t.month - 1]}';
}

/// Plain words for the numbers the server sends, keyed by the setting's name.
const settingWords = {
  'drops': ('Alert after', 'drops in an hour'),
  'after': ('Tell me if it stays offline for', 'minutes'),
  'limit': ('Alert when more than', 'people'),
  'length': ('Alert when more than', 'people waiting'),
  'minutes': ('for at least', 'minutes'),
  'wait_minutes': ('and the wait is over (0 to ignore)', 'minutes'),
  'seconds': ('Alert after', 'seconds'),
  'people': ('Alert when more than', 'people inside'),
  'max_hours': ('Alert when a vehicle stays more than', 'hours'),
  'percent': ('Alert at', '% used'),
  'days': ('Remind me', 'days before it is due'),
  'hour': ('Send it at', "o'clock"),
};

String _clock(num h) {
  final n = h.toInt();
  return '${n % 12 == 0 ? 12 : n % 12} ${n < 12 ? 'am' : 'pm'}';
}

/// A camera's own settings, as the owner sees them when setting it up.
class CameraSettings {
  final String id, name, stream;     // stream: sub (light, enough to count people) or main (full quality, for number plates)
  final bool useFootfall, useAnpr, enabled;
  final List<CameraZone> zones;
  const CameraSettings(this.id, this.name, {required this.useFootfall, required this.useAnpr, required this.enabled, required this.stream, this.zones = const []});
  CameraZone? zone(String kind) => zones.where((z) => z.kind == kind).firstOrNull;
}

/// The counting line (count_line: two points and which way is "in") or the plate area (anpr_zone: a shape), as fractions of the picture.
class CameraZone {
  final String kind;
  final List<Pt> points;
  final Pt? inDirection;
  const CameraZone(this.kind, this.points, {this.inDirection});
}

/// People counted over a period, by hour or by day.
class FootfallRange {
  final int peopleIn, peopleOut;
  final String? peakLabel;
  final List<FootfallBar> bars;
  const FootfallRange(this.peopleIn, this.peopleOut, this.peakLabel, this.bars);
  int get net => peopleIn - peopleOut;
}

class FootfallBar {
  final String label;
  final int peopleIn, peopleOut;
  const FootfallBar(this.label, this.peopleIn, this.peopleOut);
}

/// One number plate read.
class PlateRead {
  final String id, cameraId;
  final String? plate, vehicle, direction;   // direction: in or out
  final DateTime? seen;
  final bool blocked, needsReview, hasPicture;
  final int? confidence, dwellMinutes;
  const PlateRead(this.id, this.cameraId, this.plate, this.seen, {this.vehicle, this.direction, this.blocked = false, this.needsReview = false, this.hasPicture = false, this.confidence, this.dwellMinutes});
}

class PlatePage {
  final List<PlateRead> items;
  final String? next;
  const PlatePage(this.items, this.next);
}

/// Today's people count, from the cameras that count people.
class Footfall {
  final int peopleIn, peopleOut;
  final String? peakHour;
  const Footfall(this.peopleIn, this.peopleOut, this.peakHour);
  int get insideNow => (peopleIn - peopleOut).clamp(0, 1 << 30);

  factory Footfall.fromJson(Map<String, dynamic> j) {
    final peak = j['peak_hour'] as Map?;
    final t = peak == null ? null : DateTime.tryParse(peak['bucket'] as String)?.toLocal();
    final h = t == null ? null : (t.hour % 12 == 0 ? 12 : t.hour % 12);
    return Footfall(j['people_in'] as int, j['people_out'] as int, t == null ? null : '$h ${t.hour < 12 ? 'am' : 'pm'}');
  }
}

/// Who is signed in and which business they belong to.
class Me {
  final String name, business, role;
  final String? plan;
  final int? camerasInUse, cameraLimit;
  const Me(this.name, this.business, this.role, {this.plan, this.camerasInUse, this.cameraLimit});
  String get first => name.split(RegExp(r'[ ,(]')).first;
  factory Me.fromJson(Map<String, dynamic> j) => Me(j['user']['name'] as String, j['tenant']['name'] as String, (j['user']['role'] ?? '') as String,
      plan: j['tenant']['plan_name'] as String?, camerasInUse: (j['tenant']['cameras_in_use'] as num?)?.toInt(), cameraLimit: (j['tenant']['camera_limit'] as num?)?.toInt());
}

/// Alerts of the same kind on the same camera or area, shown as one row: "Too many people · Aisle 1 · 9 times today".
class AlertGroup {
  final List<AlertItem> items; // newest first
  AlertGroup(this.items);
  AlertItem get latest => items.first;
  int get count => items.length;
  bool get smart => latest.smart;
  bool get open => items.any((a) => a.state == AlertState.open);
  List<AlertItem> get openItems => items.where((a) => a.state == AlertState.open).toList();
  String get key => '${latest.rule}|${latest.where}';
}

/// Groups alerts by kind and place, newest first. [now] is only for tests.
List<AlertGroup> groupAlerts(List<AlertItem> alerts) {
  final map = <String, List<AlertItem>>{};
  for (final a in alerts) {
    (map['${a.rule}|${a.where}'] ??= []).add(a);
  }
  final groups = [for (final l in map.values) AlertGroup(l..sort((a, b) => (b.at ?? DateTime(2000)).compareTo(a.at ?? DateTime(2000))))];
  groups.sort((a, b) => (b.latest.at ?? DateTime(2000)).compareTo(a.latest.at ?? DateTime(2000)));
  return groups;
}

bool isRecent(AlertItem a, {DateTime? now}) => a.at == null || (now ?? DateTime.now()).difference(a.at!).inHours < 24;

/// "9 times, last at 4:29 pm" for a group of repeats.
String repeatsLine(AlertGroup g) {
  if (g.count == 1) return [g.latest.where, g.latest.when].where((x) => x.isNotEmpty).join(' · ');
  final last = g.latest.at == null ? '' : ', last ${friendlyWhen(g.latest.at!.toIso8601String())}';
  return [g.latest.where, '${g.count} times$last'].where((x) => x.isNotEmpty).join(' · ');
}
