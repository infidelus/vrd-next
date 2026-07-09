The headline feature in this release:

## 🎛️ Encoder speed and quality, if you want them

Output profiles that re-encode — HEVC output, or cropping — now expose two
options in the profile editor:

- **Encoder speed** (Slower → Fastest). A slower preset gives better quality for
  the same file size, at the cost of time.
- **Quality (CRF)**. A lower number means better quality and a bigger file.

Both default to exactly what VRD Next has always used, so **your existing
profiles produce identical output** and there's nothing to change unless you want
to. They're greyed out for lossless-copy profiles, where they'd do nothing.

Values that would cause trouble are guarded: the CRF is range-checked, and
unusually low or high settings ask for confirmation, explaining why.

## Also

- The profile editor sizes itself to its contents, so no rows are clipped —
  including in translations, where the text is longer.
- The timeline's cut regions are a slightly lighter red, making them easier to
  pick out from the kept scenes.
