import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../core/config.dart';

/// One marketplace search link for a product result.
class Marketplace {
  const Marketplace({required this.name, required this.region, required this.url});
  final String name;
  final String region;
  final String url;

  factory Marketplace.fromJson(Map<String, dynamic> j) => Marketplace(
        name: j['name'] as String? ?? '',
        region: j['region'] as String? ?? '',
        url: j['search_url'] as String? ?? '',
      );
}

/// A web page whose image matched the product.
class MatchingPage {
  const MatchingPage({required this.title, required this.url});
  final String title;
  final String url;

  factory MatchingPage.fromJson(Map<String, dynamic> j) => MatchingPage(
        title: j['title'] as String? ?? '(no title)',
        url: j['url'] as String? ?? '',
      );
}

/// A single detected landmark.
class LandmarkResult {
  const LandmarkResult({
    required this.name,
    required this.confidence,
    this.lat,
    this.lng,
    this.mapsUrl,
    this.description,
    this.wikipediaUrl,
  });

  final String name;
  final double confidence; // 0..1
  final double? lat;
  final double? lng;
  final String? mapsUrl;
  final String? description;
  final String? wikipediaUrl;

  bool get hasLocation => lat != null && lng != null;

  factory LandmarkResult.fromJson(Map<String, dynamic> j) {
    final loc = (j['location'] as Map?)?.cast<String, dynamic>();
    return LandmarkResult(
      name: j['name'] as String? ?? 'Unknown',
      confidence: (j['confidence'] as num?)?.toDouble() ?? 0.0,
      lat: (loc?['lat'] as num?)?.toDouble(),
      lng: (loc?['lng'] as num?)?.toDouble(),
      mapsUrl: j['maps_url'] as String?,
      description: j['description'] as String?,
      wikipediaUrl: j['wikipedia_url'] as String?,
    );
  }
}

/// An identified product (Vision web detection + optional Gemini explanation).
class ProductResult {
  const ProductResult({
    required this.name,
    required this.categories,
    this.aiExplanation,
    required this.marketplaces,
    required this.matchingPages,
    required this.similarImages,
  });

  final String name;
  final List<String> categories;
  final String? aiExplanation;
  final List<Marketplace> marketplaces;
  final List<MatchingPage> matchingPages;
  final List<String> similarImages;

  factory ProductResult.fromJson(Map<String, dynamic> j) => ProductResult(
        name: j['product_name'] as String? ?? 'Unknown product',
        categories: ((j['categories'] as List?) ?? const [])
            .map((e) => e.toString())
            .toList(growable: false),
        aiExplanation: j['ai_explanation'] as String?,
        marketplaces: ((j['marketplaces'] as List?) ?? const [])
            .map((e) => Marketplace.fromJson((e as Map).cast<String, dynamic>()))
            .toList(growable: false),
        matchingPages: ((j['matching_pages'] as List?) ?? const [])
            .map((e) => MatchingPage.fromJson((e as Map).cast<String, dynamic>()))
            .toList(growable: false),
        similarImages: ((j['similar_images'] as List?) ?? const [])
            .map((e) => e.toString())
            .toList(growable: false),
      );
}

/// Parsed `{ok, mode, result}` detection envelope shared by the REST endpoint
/// and the `identify_image` voice tool. Exactly one of [landmarks]/[product]/
/// [lensUrl] is populated, per [mode].
class FinderDetection {
  const FinderDetection({
    required this.ok,
    required this.mode,
    this.error,
    this.landmarks = const [],
    this.product,
    this.lensUrl,
    this.source,
  });

  final bool ok;
  final String mode; // landmark | product | web
  final String? error;
  final List<LandmarkResult> landmarks;
  final ProductResult? product;
  final String? lensUrl;

  /// Who identified the subject — e.g. "Google Vision API", "Gemini AI", or
  /// both. Surfaced in the result card so the user knows the source (Google
  /// Vision-identified results are credited to the Vision API). Null if unknown.
  final String? source;

  bool get isLandmark => mode == 'landmark';
  bool get isProduct => mode == 'product';

  /// True when the call succeeded but found nothing to show.
  bool get isEmpty =>
      ok &&
      landmarks.isEmpty &&
      product == null &&
      (lensUrl == null || lensUrl!.isEmpty) &&
      (product?.name.isEmpty ?? true);

  /// Parse the universal `{ok, mode, result}` envelope.
  factory FinderDetection.fromEnvelope(Map<String, dynamic> env) {
    final ok = env['ok'] as bool? ?? false;
    if (!ok) {
      return FinderDetection(
        ok: false,
        mode: env['mode'] as String? ?? 'unknown',
        error: (env['error'] ?? env['message']) as String?,
      );
    }
    final mode = env['mode'] as String? ?? 'unknown';
    final result = (env['result'] as Map?)?.cast<String, dynamic>() ?? const {};
    final source = result['source'] as String?;
    switch (mode) {
      case 'landmark':
        final list = ((result['landmarks'] as List?) ?? const [])
            .map((e) => LandmarkResult.fromJson((e as Map).cast<String, dynamic>()))
            .toList(growable: false);
        return FinderDetection(
            ok: true, mode: mode, landmarks: list, source: source);
      case 'product':
        return FinderDetection(
          ok: true,
          mode: mode,
          product: ProductResult.fromJson(result),
          source: source,
        );
      case 'web':
        return FinderDetection(
          ok: true,
          mode: mode,
          lensUrl: result['lens_url'] as String?,
        );
      default:
        return FinderDetection(ok: true, mode: mode);
    }
  }
}

/// Raised when the request itself fails (network/timeout/non-200). Detection
/// failures (bad image, no key) arrive as a normal envelope with `ok: false`.
class FinderException implements Exception {
  FinderException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// Thin REST client for the backend's `POST /detect` endpoint. Points at the
/// same backend the live session uses (via [AppConfig.httpBase]).
class FinderApi {
  FinderApi(this._config, {http.Client? client})
      : _client = client ?? http.Client();

  AppConfig _config;
  final http.Client _client;

  void updateConfig(AppConfig config) => _config = config;

  static const _timeout = Duration(seconds: 30);

  Uri get _uri => _config.httpBase.replace(path: '/detect');

  /// Detect a landmark/product. Pass [imageBytes] (a JPEG) or [imageUrl].
  /// [mode] is `auto` | `landmark` | `product`.
  Future<FinderDetection> detect({
    Uint8List? imageBytes,
    String? imageUrl,
    String mode = 'auto',
    String? lang,
  }) async {
    final body = <String, dynamic>{'mode': mode};
    if (lang != null && lang.isNotEmpty) body['lang'] = lang;
    if (imageBytes != null) {
      body['image_data'] = base64Encode(imageBytes);
    } else if (imageUrl != null) {
      body['image_url'] = imageUrl;
    } else {
      throw FinderException('No image to identify.');
    }

    final http.Response r;
    try {
      r = await _client
          .post(
            _uri,
            headers: const {'Content-Type': 'application/json'},
            body: jsonEncode(body),
          )
          .timeout(_timeout);
    } catch (e) {
      throw FinderException('Network error — check your internet and try again.');
    }

    if (r.statusCode != 200) {
      throw FinderException('Server error (${r.statusCode}). Please try again.');
    }
    try {
      final env = jsonDecode(r.body) as Map<String, dynamic>;
      return FinderDetection.fromEnvelope(env);
    } catch (e) {
      throw FinderException('Could not read the server response.');
    }
  }

  void dispose() => _client.close();
}
