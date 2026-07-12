import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/theme.dart';
import '../../data/finder_api.dart';

/// Renders a [FinderDetection] result — landmark cards, a product card, a free
/// Lens link, or a friendly empty/error state. Shared by the Finder screen, the
/// live-camera scan sheet, and the voice (`identify_image`) sheet so all three
/// look identical.
class FinderResultView extends StatelessWidget {
  const FinderResultView(this.detection, {super.key});

  final FinderDetection detection;

  @override
  Widget build(BuildContext context) {
    if (!detection.ok) {
      return _Message(
        icon: Icons.error_outline,
        color: Aurora.danger,
        title: 'Something went wrong',
        body: detection.error ?? 'Try again with a clearer photo.',
      );
    }
    if (detection.isEmpty) {
      return const _Message(
        icon: Icons.search_off,
        color: Aurora.amber,
        title: 'Nothing recognized',
        body: 'No known place or product was found in this image. '
            'Try a clear photo of a single subject.',
      );
    }

    if (detection.isLandmark) {
      return Column(
        children: [
          for (final lm in detection.landmarks)
            _LandmarkCard(lm, source: detection.source),
        ],
      );
    }
    if (detection.isProduct && detection.product != null) {
      return _ProductCard(detection.product!, source: detection.source);
    }
    if (detection.lensUrl != null) {
      return _Message(
        icon: Icons.image_search,
        color: Aurora.mint,
        title: 'Google Lens',
        body: 'Tap to open full Lens results in your browser.',
        action: ('Open Lens', () => _open(detection.lensUrl!)),
      );
    }
    return const SizedBox.shrink();
  }
}

Future<void> _open(String url) async {
  final uri = Uri.tryParse(url);
  if (uri != null) {
    await launchUrl(uri, mode: LaunchMode.externalApplication);
  }
}

// ---- Landmark --------------------------------------------------------------

class _LandmarkCard extends StatelessWidget {
  const _LandmarkCard(this.lm, {this.source});
  final LandmarkResult lm;
  final String? source;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return _Card(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.account_balance, color: Aurora.mint, size: 22),
              const SizedBox(width: 8),
              Expanded(
                child: Text(lm.name, style: theme.textTheme.titleMedium),
              ),
              _ConfidenceBadge(lm.confidence),
            ],
          ),
          if (lm.description != null && lm.description!.isNotEmpty) ...[
            const SizedBox(height: 12),
            _ExpandableText(lm.description!),
          ],
          const SizedBox(height: 14),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              if (lm.mapsUrl != null)
                _LinkChip(
                  icon: Icons.map_outlined,
                  label: 'Open in Maps',
                  onTap: () => _open(lm.mapsUrl!),
                ),
              if (lm.wikipediaUrl != null)
                _LinkChip(
                  icon: Icons.menu_book_outlined,
                  label: 'Wikipedia',
                  onTap: () => _open(lm.wikipediaUrl!),
                ),
            ],
          ),
          if (source != null && source!.isNotEmpty) _SourceAttribution(source!),
        ],
      ),
    );
  }
}

class _ConfidenceBadge extends StatelessWidget {
  const _ConfidenceBadge(this.confidence);
  final double confidence;

  @override
  Widget build(BuildContext context) {
    final pct = (confidence * 100).round();
    final color = pct >= 75
        ? Aurora.teal
        : (pct >= 50 ? Aurora.amber : Aurora.textMuted);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: Aurora.tint(color, 0.18),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(
        '$pct% match',
        style: TextStyle(color: color, fontWeight: FontWeight.w600, fontSize: 12),
      ),
    );
  }
}

// ---- Product ---------------------------------------------------------------

class _ProductCard extends StatelessWidget {
  const _ProductCard(this.p, {this.source});
  final ProductResult p;
  final String? source;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    // Group marketplaces by region, preserving insertion order.
    final byRegion = <String, List<Marketplace>>{};
    for (final m in p.marketplaces) {
      byRegion.putIfAbsent(m.region, () => []).add(m);
    }

    return _Card(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.shopping_bag_outlined,
                  color: Aurora.purpleSoft, size: 22),
              const SizedBox(width: 8),
              Expanded(child: Text(p.name, style: theme.textTheme.titleMedium)),
            ],
          ),
          if (p.categories.isNotEmpty) ...[
            const SizedBox(height: 12),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final c in p.categories) _Chip(c),
              ],
            ),
          ],
          if (p.aiExplanation != null && p.aiExplanation!.isNotEmpty) ...[
            const SizedBox(height: 14),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: Aurora.tint(Aurora.purple, 0.12),
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: Aurora.tint(Aurora.purple, 0.25)),
              ),
              child: Text(
                p.aiExplanation!,
                style: theme.textTheme.bodyMedium
                    ?.copyWith(color: Aurora.textPrimary),
              ),
            ),
          ],
          if (p.marketplaces.isNotEmpty) ...[
            const SizedBox(height: 16),
            Text('Where to buy', style: theme.textTheme.titleSmall),
            const SizedBox(height: 8),
            for (final entry in byRegion.entries) ...[
              Padding(
                padding: const EdgeInsets.only(top: 6, bottom: 4),
                child: Text(entry.key.toUpperCase(),
                    style: const TextStyle(
                        color: Aurora.textMuted,
                        fontSize: 11,
                        letterSpacing: 0.6)),
              ),
              for (final m in entry.value)
                _MarketRow(m),
            ],
          ],
          if (p.matchingPages.isNotEmpty) ...[
            const SizedBox(height: 16),
            Text('On the web', style: theme.textTheme.titleSmall),
            const SizedBox(height: 6),
            for (final page in p.matchingPages.take(4))
              _LinkRow(icon: Icons.link, label: page.title, onTap: () => _open(page.url)),
          ],
          if (p.similarImages.isNotEmpty) ...[
            const SizedBox(height: 16),
            Text('Similar images', style: theme.textTheme.titleSmall),
            const SizedBox(height: 8),
            SizedBox(
              height: 84,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                itemCount: p.similarImages.length,
                separatorBuilder: (_, __) => const SizedBox(width: 8),
                itemBuilder: (_, i) => ClipRRect(
                  borderRadius: BorderRadius.circular(10),
                  child: Image.network(
                    p.similarImages[i],
                    width: 84,
                    height: 84,
                    fit: BoxFit.cover,
                    errorBuilder: (_, __, ___) => Container(
                      width: 84,
                      height: 84,
                      color: Aurora.glass,
                      child: const Icon(Icons.broken_image_outlined,
                          color: Aurora.textMuted),
                    ),
                  ),
                ),
              ),
            ),
          ],
          if (source != null && source!.isNotEmpty) _SourceAttribution(source!),
        ],
      ),
    );
  }
}

/// A subtle "Identified via <source>" line crediting who recognised the
/// subject — e.g. "Google Vision API" when Google Vision did the identifying.
class _SourceAttribution extends StatelessWidget {
  const _SourceAttribution(this.source);
  final String source;

  @override
  Widget build(BuildContext context) {
    final usesVision = source.contains('Vision');
    return Padding(
      padding: const EdgeInsets.only(top: 14),
      child: Row(
        children: [
          Icon(
            usesVision ? Icons.visibility_outlined : Icons.auto_awesome_outlined,
            size: 14,
            color: Aurora.textMuted,
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              'Identified via $source',
              style: const TextStyle(color: Aurora.textMuted, fontSize: 12),
            ),
          ),
        ],
      ),
    );
  }
}

class _MarketRow extends StatelessWidget {
  const _MarketRow(this.m);
  final Marketplace m;

  @override
  Widget build(BuildContext context) {
    return _LinkRow(
      icon: Icons.open_in_new,
      label: m.name,
      onTap: () => _open(m.url),
    );
  }
}

// ---- Shared bits -----------------------------------------------------------

class _Card extends StatelessWidget {
  const _Card({required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        margin: const EdgeInsets.only(bottom: 12),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: Aurora.surfaceHigh,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: Aurora.glassBorder),
        ),
        child: child,
      );
}

class _Chip extends StatelessWidget {
  const _Chip(this.label);
  final String label;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
          color: Aurora.glass,
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: Aurora.glassBorder),
        ),
        child: Text(label,
            style: const TextStyle(color: Aurora.textPrimary, fontSize: 12)),
      );
}

class _LinkChip extends StatelessWidget {
  const _LinkChip({required this.icon, required this.label, required this.onTap});
  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Material(
        color: Aurora.tint(Aurora.teal, 0.16),
        borderRadius: BorderRadius.circular(24),
        child: InkWell(
          borderRadius: BorderRadius.circular(24),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(icon, size: 16, color: Aurora.mint),
                const SizedBox(width: 6),
                Text(label,
                    style: const TextStyle(
                        color: Aurora.mint, fontWeight: FontWeight.w600)),
              ],
            ),
          ),
        ),
      );
}

class _LinkRow extends StatelessWidget {
  const _LinkRow({required this.icon, required this.label, required this.onTap});
  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => InkWell(
        borderRadius: BorderRadius.circular(8),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 9),
          child: Row(
            children: [
              Icon(icon, size: 18, color: Aurora.textMuted),
              const SizedBox(width: 10),
              Expanded(
                child: Text(label,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: Aurora.textPrimary)),
              ),
              const Icon(Icons.chevron_right, size: 18, color: Aurora.textMuted),
            ],
          ),
        ),
      );
}

class _ExpandableText extends StatefulWidget {
  const _ExpandableText(this.text);
  final String text;

  @override
  State<_ExpandableText> createState() => _ExpandableTextState();
}

class _ExpandableTextState extends State<_ExpandableText> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          widget.text,
          maxLines: _expanded ? null : 3,
          overflow: _expanded ? TextOverflow.visible : TextOverflow.ellipsis,
          style: theme.textTheme.bodyMedium?.copyWith(color: Aurora.textMuted),
        ),
        if (widget.text.length > 140)
          GestureDetector(
            onTap: () => setState(() => _expanded = !_expanded),
            child: Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(_expanded ? 'Show less' : 'Read more',
                  style: const TextStyle(
                      color: Aurora.mint, fontWeight: FontWeight.w600)),
            ),
          ),
      ],
    );
  }
}

class _Message extends StatelessWidget {
  const _Message({
    required this.icon,
    required this.color,
    required this.title,
    required this.body,
    this.action,
  });

  final IconData icon;
  final Color color;
  final String title;
  final String body;
  final (String, VoidCallback)? action;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return _Card(
      child: Column(
        children: [
          Icon(icon, color: color, size: 40),
          const SizedBox(height: 12),
          Text(title, style: theme.textTheme.titleMedium),
          const SizedBox(height: 6),
          Text(body,
              textAlign: TextAlign.center,
              style: theme.textTheme.bodyMedium
                  ?.copyWith(color: Aurora.textMuted)),
          if (action != null) ...[
            const SizedBox(height: 14),
            FilledButton(onPressed: action!.$2, child: Text(action!.$1)),
          ],
        ],
      ),
    );
  }
}
