import 'package:geocoding/geocoding.dart';
import 'package:geolocator/geolocator.dart';

import 'logger.dart';

/// A resolved device location: coordinates plus a best-effort human address.
class LocationFix {
  const LocationFix({required this.lat, required this.lng, this.address});

  final double lat;
  final double lng;
  final String? address;

  Map<String, dynamic> toJson() => {
        'lat': lat,
        'lng': lng,
        if (address != null && address!.isNotEmpty) 'address': address,
      };
}

/// Fetches the current GPS location and reverse-geocodes it to an address so
/// the assistant can answer "where am I?". All failures (services off,
/// permission denied, timeout) resolve to `null` rather than throwing — the
/// session continues; the model just won't have a location.
class LocationService {
  LocationService._();

  static final _log = Logger('LocationService');

  static Future<LocationFix?> current() async {
    try {
      if (!await Geolocator.isLocationServiceEnabled()) {
        _log.warn('location services are disabled');
        return null;
      }
      var perm = await Geolocator.checkPermission();
      if (perm == LocationPermission.denied) {
        perm = await Geolocator.requestPermission();
      }
      if (perm == LocationPermission.denied ||
          perm == LocationPermission.deniedForever) {
        _log.warn('location permission not granted: $perm');
        return null;
      }

      final pos = await Geolocator.getCurrentPosition(
        desiredAccuracy: LocationAccuracy.high,
        timeLimit: const Duration(seconds: 12),
      );

      final address = await _reverseGeocode(pos.latitude, pos.longitude);
      _log.info('location fix ${pos.latitude},${pos.longitude} ($address)');
      return LocationFix(
        lat: pos.latitude,
        lng: pos.longitude,
        address: address,
      );
    } catch (e) {
      _log.warn('location fetch failed: $e');
      return null;
    }
  }

  static Future<String?> _reverseGeocode(double lat, double lng) async {
    try {
      final marks = await placemarkFromCoordinates(lat, lng);
      if (marks.isEmpty) return null;
      final p = marks.first;
      final parts = <String?>[
        p.name,
        p.subLocality,
        p.locality,
        p.administrativeArea,
        p.country,
      ];
      // De-duplicate while preserving order (name often repeats subLocality).
      final seen = <String>{};
      final out = <String>[];
      for (final part in parts) {
        final s = (part ?? '').trim();
        if (s.isNotEmpty && seen.add(s)) out.add(s);
      }
      return out.isEmpty ? null : out.join(', ');
    } catch (e) {
      _log.warn('reverse geocode failed: $e');
      return null;
    }
  }
}
