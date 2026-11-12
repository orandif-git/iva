import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models.dart';
import '../repo.dart';

/// The signed-in customer's data, available to every screen without threading it through constructors.
///
/// The app is being moved to Riverpod one surface at a time: screens that still take a `repo` keep working, while new
/// screens (the rule builder) read it from here. [IVisionApp] overrides this with the signed-in repository.
final repositoryProvider = Provider<Repository>((ref) => throw StateError('No repository was provided. Wrap the app in a ProviderScope override.'));

/// The customer's rules. A rule screen watches this, and a change refreshes every list that shows it.
final rulesProvider = FutureProvider.autoDispose<List<Rule>>((ref) => ref.watch(repositoryProvider).rules());

/// The people who can be told about an alert.
final peopleProvider = FutureProvider.autoDispose<List<Person>>((ref) => ref.watch(repositoryProvider).people());

/// Where a rule applies, in words the shop owner uses.
String rulePlace(Rule r, List<CameraItem> cameras, List<AreaInfo> areas) {
  if (r.zoneId != null) {
    final area = areas.where((a) => a.id == r.zoneId).firstOrNull;
    if (area != null) return area.cameraName.isEmpty ? area.kindLabel : '${area.cameraName} · ${area.kindLabel}';
  }
  if (r.cameraId != null) {
    return cameras.where((c) => c.id == r.cameraId).map((c) => c.name).firstOrNull ?? 'A camera';
  }
  return 'Every camera';
}

extension on AreaInfo {
  /// "Queue", "People in an area", "Staff area": the words on the area screen.
  String get kindLabel => switch (kind) {
        'queue_area' => 'Queue',
        'area_count' => 'People in an area',
        'staff_area' => 'Staff area',
        _ => 'Marked area',
      };
}
