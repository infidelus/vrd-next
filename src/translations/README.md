# Translations

VRD Next's interface can be translated. English is built in; other languages are
added here as Qt translation files.

- `vrd-next_en.ts` — the **template**: every translatable phrase in the app, in
  English. This is regenerated from the source and is what you translate *from*.
- `vrd-next_<code>.ts` — a **translation** for one language (`de` = German,
  `fr` = French, and so on — standard two-letter language codes).
- `vrd-next_<code>.qm` — the **compiled** version of that translation, which is
  what the application actually loads. You don't edit these by hand.

## Adding a language

1. **Copy the template.** Copy `vrd-next_en.ts` to `vrd-next_<code>.ts` (for
   example `vrd-next_de.ts` for German).

2. **Translate it.** Either open it in **Qt Linguist**, or — since it's just XML
   — hand the file to a translator or a chatbot and ask it to fill in each
   `<translation>` tag with the translation of the `<source>` above it, leaving
   everything else untouched. A snippet looks like this:

   ```xml
   <message>
       <source>File</source>
       <translation>Datei</translation>
   </message>
   ```

3. **Compile it.** Run the helper, which turns every `.ts` in this folder into a
   `.qm`:

   ```sh
   ./compile.sh
   ```

   (Under the bonnet that's `pyside6-lrelease vrd-next_<code>.ts`, which ships
   with PySide6.)

4. **Use it.** Restart VRD Next and pick the language under
   **Settings → General → Language**. It appears there automatically once its
   `.qm` is present.

## Updating a translation after the app changes

When new text is added to VRD Next, refresh the template and merge the new
phrases into each translation (existing translations are kept):

```sh
pyside6-lupdate ../**/*.py -ts vrd-next_en.ts vrd-next_de.ts vrd-next_fr.ts
```

Then translate any newly-added (empty) entries and run `./compile.sh` again.

## Checking your work

`./compile.sh` prints a count as it goes, for example
`Generated 675 translation(s) (675 finished and 0 unfinished)`. That's the quickest
way to see whether anything was missed — "unfinished" means an entry still has an
empty `<translation>`.

Two things to watch for, especially if you translate by handing the file to an AI:

- **Never change a `<source>`.** It's the key the running application looks the
  translation up by, so a "tidied" English string silently stops matching and the
  text stays English. If a `<source>` is altered, the next `pyside6-lupdate` puts
  the original back as a new, untranslated entry — so a rising "unfinished" count
  after a translation pass usually means a source was edited.
- **Translate the whole file, in chunks if need be.** Ask for the actual entries
  in the file rather than a list of phrases from memory; it's easy to end up with
  translations for strings that don't exist while real ones stay empty.

The German translation is complete, so it doubles as a worked example of what a
finished file looks like.

## Translating the user guide

The user guide is a separate HTML file, not part of the `.ts`. To translate it,
copy `../assets/help/user-guide.html` to `user-guide_<code>.html` in the same
folder (for example `user-guide_de.html`) and translate the text inside it,
leaving the HTML tags alone. VRD Next shows the guide for the chosen language if
that file exists, and falls back to the English guide if it doesn't — so a
missing translation never leaves the reader with nothing.
