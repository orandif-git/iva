/// How long a view stays open. The same numbers as the server (backend `services/live.py`), which enforces them.
enum LiveMode { live, ai }

class LiveRules {
  final int period, cap;
  const LiveRules(this.period, this.cap);
  static const live = LiveRules(120, 600); // closes after 2 minutes, "keep watching" adds 2, up to 10 minutes in all
  static const ai = LiveRules(30, 120); //   closes after 30 seconds, adds 30, up to 2 minutes in all
  static LiveRules of(LiveMode m) => m == LiveMode.ai ? ai : live;
}

/// The clock of one view. It only counts; the screen ticks it once a second and the server has the final say.
class LiveClock {
  final LiveMode mode;
  int left, elapsed = 0, extensions = 0;
  bool stopped = false;

  LiveClock(this.mode) : left = LiveRules.of(mode).period;

  LiveRules get rules => LiveRules.of(mode);
  bool get ended => stopped || left <= 0;
  bool get canExtend => !ended && elapsed + left + rules.period <= rules.cap;
  bool get connecting => !ended && elapsed < 2;

  void tick() {
    if (ended) return;
    left -= 1;
    elapsed += 1;
  }

  bool extend() {
    if (!canExtend) return false;
    left += rules.period;
    extensions += 1;
    return true;
  }

  void stop() => stopped = true;

  String get countdown => '${left ~/ 60}:${(left % 60).toString().padLeft(2, '0')}';
}
