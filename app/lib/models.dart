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
  bool smart;
  AlertState state;
  AlertItem({required this.id, required this.title, required this.body, required this.rule, required this.where, required this.when, this.smart = false, this.state = AlertState.open});

  factory AlertItem.fromJson(Map<String, dynamic> j) => AlertItem(
        id: j['id'],
        title: j['title'],
        body: j['body'] ?? '',
        rule: j['rule_type'] ?? '',
        where: j['camera_name'] ?? '',
        when: friendlyWhen((j['created_at'] ?? '') as String),
        state: AlertState.values.firstWhere((s) => s.name == j['state'], orElse: () => AlertState.open),
      );
}

class CameraItem {
  final String id, name, recorder;
  final bool working;
  const CameraItem(this.id, this.name, this.recorder, this.working);
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

  /// "after 15 min", "over 6 people for 5 min": a short line for My alerts.
  String get summary {
    if (!enabled && params.isEmpty) return description;
    final bits = [for (final s in settings) if (params[s.key] != null) '${s.label.toLowerCase()} ${params[s.key]} ${s.unit}'.trim()];
    return bits.isEmpty ? description : bits.join(' · ');
  }
}

class Person {
  final String id, name, phone;
  const Person(this.id, this.name, this.phone);
}

class ReportItem {
  final String name, when;
  final bool on;
  const ReportItem(this.name, this.when, this.on);
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
  'drops': ('Tell me after this many drops in an hour', 'drops'),
  'after': ('Tell me if it stays offline for', 'minutes'),
  'limit': ('Alert when more than', 'people'),
  'length': ('Alert when more than', 'people waiting'),
  'minutes': ('for at least', 'minutes'),
  'wait_minutes': ('and the wait is over (0 to ignore)', 'minutes'),
  'seconds': ('Alert after', 'seconds'),
};

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
