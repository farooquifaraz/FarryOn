import 'package:flutter/material.dart';

import '../../../core/theme.dart';
import '../glasses_lab_controller.dart';
import 'lab_card.dart';

/// Card 4 — the audio A/B test that answers Stage 0's biggest unknown:
/// which input path (Classic-BT HFP vs SDK `voiceFromGlasses` PCM) gives
/// usable 16 kHz speech for the live pipeline, and does TTS play cleanly on
/// the glasses speaker.
class AudioCard extends StatefulWidget {
  const AudioCard(this.c, {super.key});

  final GlassesLabController c;

  @override
  State<AudioCard> createState() => _AudioCardState();
}

class _AudioCardState extends State<AudioCard> {
  /// Local slider position (0–100 %); sent to the glasses on release.
  double _volume = 50;

  GlassesLabController get c => widget.c;

  @override
  Widget build(BuildContext context) {
    final connected = c.connectionState == 'connected';
    final testing = c.audioMode != null;
    return LabCard(
      icon: Icons.graphic_eq,
      title: 'Audio paths',
      status: testing ? 'testing: ${c.audioMode}' : null,
      statusColor: Aurora.amber,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              OutlinedButton.icon(
                onPressed: connected && !testing ? c.pairClassicBt : null,
                icon: const Icon(Icons.headset, size: 18),
                label: const Text('Pair Classic BT'),
              ),
              FilledButton.icon(
                onPressed: connected && !testing
                    ? () => c.startAudioTest('hfp')
                    : null,
                icon: const Icon(Icons.mic, size: 18),
                label: const Text('Mic via HFP'),
              ),
              FilledButton.icon(
                onPressed: connected && !testing
                    ? () => c.startAudioTest('pcm')
                    : null,
                icon: const Icon(Icons.settings_voice, size: 18),
                label: const Text('Mic via SDK PCM'),
              ),
              OutlinedButton.icon(
                onPressed: connected && !testing
                    ? () => c.startAudioTest('tts')
                    : null,
                icon: const Icon(Icons.volume_up, size: 18),
                label: const Text('Play TTS sample'),
              ),
              // Undocumented aiVoiceWake() probe: can the APP open the
              // glasses mic with no touch? Huge for hands-free Stage B.
              OutlinedButton.icon(
                onPressed: connected && !testing
                    ? () => c.startAudioTest('wake')
                    : null,
                icon: const Icon(Icons.record_voice_over, size: 18),
                label: const Text('Wake glasses mic'),
              ),
              if (testing)
                OutlinedButton.icon(
                  onPressed: c.stopAudioTest,
                  icon: const Icon(Icons.stop, size: 18),
                  label: const Text('Stop'),
                ),
            ],
          ),
          const SizedBox(height: 4),
          Row(
            children: [
              const Icon(Icons.volume_down, size: 18, color: Aurora.textMuted),
              Expanded(
                child: Slider(
                  value: _volume,
                  min: 0,
                  max: 100,
                  divisions: 10,
                  label: '${_volume.round()}%',
                  onChanged:
                      connected ? (v) => setState(() => _volume = v) : null,
                  onChangeEnd: connected
                      ? (v) => c.setVolume('music', v.round())
                      : null,
                ),
              ),
              const Icon(Icons.volume_up, size: 18, color: Aurora.textMuted),
            ],
          ),
          const SizedBox(height: 6),
          if (c.audioMode == 'pcm' || c.pcmChunks > 0)
            LabKv(
              'PCM stream',
              '${c.pcmChunks} chunks'
              '${c.pcmSampleRate != null ? '  ·  ${c.pcmSampleRate} Hz' : ''}',
            )
          else
            const Text(
              'Goal: decide the Stage B input path. HFP = zero extra code but '
              'may be 8 kHz narrowband; SDK PCM = raw stream, format unverified.',
              style: TextStyle(color: Aurora.textMuted, fontSize: 12.5),
            ),
        ],
      ),
    );
  }
}
