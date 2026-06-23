import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';

import '../../core/theme.dart';
import '../../data/finder_api.dart';
import '../../state/providers.dart';
import 'finder_result_view.dart';

/// Flow #1 — the dedicated Finder screen: pick/capture a photo and identify the
/// landmark or product in it. Empty → preview → loading → result/error states.
class FinderScreen extends ConsumerStatefulWidget {
  const FinderScreen({super.key});

  @override
  ConsumerState<FinderScreen> createState() => _FinderScreenState();
}

enum _Mode { auto, landmark, product }

class _FinderScreenState extends ConsumerState<FinderScreen> {
  final _picker = ImagePicker();

  _Mode _mode = _Mode.auto;
  Uint8List? _image;
  bool _loading = false;
  FinderDetection? _result;
  String? _error;

  Future<void> _pick(ImageSource source) async {
    try {
      final file = await _picker.pickImage(
        source: source,
        maxWidth: 1280,
        maxHeight: 1280,
        imageQuality: 85,
      );
      if (file == null) return;
      final bytes = await file.readAsBytes();
      setState(() {
        _image = bytes;
        _result = null;
        _error = null;
      });
      await _identify();
    } catch (e) {
      setState(() => _error = 'Couldn\'t load image: $e');
    }
  }

  Future<void> _identify() async {
    final image = _image;
    if (image == null || _loading) return;
    setState(() {
      _loading = true;
      _result = null;
      _error = null;
    });
    try {
      // Device language so the product explanation comes back in the user's
      // language (Arabic→Arabic, Hindi→Roman Hindi, etc.).
      final lang = WidgetsBinding.instance.platformDispatcher.locale.languageCode;
      final detection = await ref.read(finderApiProvider).detect(
            imageBytes: image,
            mode: _mode.name,
            lang: lang,
          );
      if (mounted) setState(() => _result = detection);
    } on FinderException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Aurora.base,
      appBar: AppBar(
        title: const Text('Finder'),
        backgroundColor: Aurora.surface,
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _ModeSelector(
                mode: _mode,
                onChanged: (m) {
                  setState(() => _mode = m);
                  if (_image != null) _identify();
                },
              ),
              const SizedBox(height: 16),
              if (_image == null)
                _EmptyState(onCamera: () => _pick(ImageSource.camera), onGallery: () => _pick(ImageSource.gallery))
              else
                _Preview(
                  image: _image!,
                  onChange: _showPickSheet,
                ),
              const SizedBox(height: 16),
              if (_loading)
                const _LoadingState()
              else if (_error != null)
                FinderResultView(FinderDetection(ok: false, mode: 'error', error: _error))
              else if (_result != null)
                FinderResultView(_result!),
            ],
          ),
        ),
      ),
    );
  }

  void _showPickSheet() {
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Aurora.surface,
      builder: (_) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.camera_alt_outlined, color: Aurora.mint),
              title: const Text('Camera'),
              onTap: () {
                Navigator.pop(context);
                _pick(ImageSource.camera);
              },
            ),
            ListTile(
              leading: const Icon(Icons.photo_library_outlined, color: Aurora.mint),
              title: const Text('Gallery'),
              onTap: () {
                Navigator.pop(context);
                _pick(ImageSource.gallery);
              },
            ),
          ],
        ),
      ),
    );
  }
}

class _ModeSelector extends StatelessWidget {
  const _ModeSelector({required this.mode, required this.onChanged});
  final _Mode mode;
  final ValueChanged<_Mode> onChanged;

  @override
  Widget build(BuildContext context) {
    return SegmentedButton<_Mode>(
      segments: const [
        ButtonSegment(value: _Mode.auto, label: Text('Auto')),
        ButtonSegment(value: _Mode.landmark, label: Text('Landmark')),
        ButtonSegment(value: _Mode.product, label: Text('Product')),
      ],
      selected: {mode},
      showSelectedIcon: false,
      onSelectionChanged: (s) => onChanged(s.first),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.onCamera, required this.onGallery});
  final VoidCallback onCamera;
  final VoidCallback onGallery;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 40, horizontal: 20),
      decoration: BoxDecoration(
        color: Aurora.surfaceHigh,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Aurora.glassBorder),
      ),
      child: Column(
        children: [
          const Icon(Icons.image_search, size: 56, color: Aurora.mint),
          const SizedBox(height: 16),
          Text('Identify anything or any place',
              textAlign: TextAlign.center, style: theme.textTheme.titleMedium),
          const SizedBox(height: 8),
          Text(
            'Take a photo or pick one from your gallery — Farry will identify the '
            'landmark or product and show the details.',
            textAlign: TextAlign.center,
            style: theme.textTheme.bodyMedium?.copyWith(color: Aurora.textMuted),
          ),
          const SizedBox(height: 24),
          Row(
            children: [
              Expanded(
                child: FilledButton.icon(
                  onPressed: onCamera,
                  icon: const Icon(Icons.camera_alt_outlined),
                  label: const Text('Camera'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: onGallery,
                  icon: const Icon(Icons.photo_library_outlined),
                  label: const Text('Gallery'),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _Preview extends StatelessWidget {
  const _Preview({required this.image, required this.onChange});
  final Uint8List image;
  final VoidCallback onChange;

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(16),
          child: Image.memory(
            image,
            width: double.infinity,
            height: 220,
            fit: BoxFit.cover,
          ),
        ),
        Positioned(
          top: 8,
          right: 8,
          child: Material(
            color: Colors.black54,
            shape: const CircleBorder(),
            child: IconButton(
              tooltip: 'Change',
              icon: const Icon(Icons.refresh, color: Colors.white),
              onPressed: onChange,
            ),
          ),
        ),
      ],
    );
  }
}

class _LoadingState extends StatelessWidget {
  const _LoadingState();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 36),
      decoration: BoxDecoration(
        color: Aurora.surfaceHigh,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Aurora.glassBorder),
      ),
      child: const Column(
        children: [
          CircularProgressIndicator(color: Aurora.mint),
          SizedBox(height: 16),
          Text('Identifying…',
              style: TextStyle(color: Aurora.textMuted)),
        ],
      ),
    );
  }
}
