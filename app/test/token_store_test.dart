import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ivision_app/api_client.dart';

void main() {
  test('a sign-in kept in preferences by an earlier version moves into the protected store and the old copy is erased', () async {
    SharedPreferences.setMockInitialValues({'gi_next_rt': 'old-refresh-token'});
    FlutterSecureStorage.setMockInitialValues({});
    final store = SecureTokenStore();
    expect(await store.read(), 'old-refresh-token', reason: 'nobody has to sign in again');
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('gi_next_rt'), isNull, reason: 'the old plain copy is gone');
    expect(await const FlutterSecureStorage().read(key: 'gi_next_rt'), 'old-refresh-token');
    expect(await SecureTokenStore().read(), 'old-refresh-token', reason: 'and it is read from the protected store from now on');
  });

  test('a new sign-in is written to the protected store and signing out erases it from both places', () async {
    SharedPreferences.setMockInitialValues({});
    FlutterSecureStorage.setMockInitialValues({});
    final store = SecureTokenStore();
    await store.write('fresh');
    expect(await const FlutterSecureStorage().read(key: 'gi_next_rt'), 'fresh');
    expect((await SharedPreferences.getInstance()).getString('gi_next_rt'), isNull);
    await store.clear();
    expect(await store.read(), isNull);
  });
}
