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
  final bool smart;
  AlertState state;
  AlertItem({required this.id, required this.title, required this.body, required this.rule, required this.where, required this.when, this.smart = false, this.state = AlertState.open});

  factory AlertItem.fromJson(Map<String, dynamic> j) => AlertItem(
        id: j['id'],
        title: j['title'],
        body: j['body'] ?? '',
        rule: j['rule_type'] ?? '',
        where: j['camera_name'] ?? '',
        when: j['created_at'] ?? '',
        state: AlertState.values.firstWhere((s) => s.name == j['state'], orElse: () => AlertState.open),
      );
}

class CameraItem {
  final String id, name, recorder;
  final bool working;
  const CameraItem(this.id, this.name, this.recorder, this.working);
}
