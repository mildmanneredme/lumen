# Private Lumen release

Author decisions recorded 10 October 2026.

| Decision | Release behavior |
| --- | --- |
| Access | Private site for approximately 50 readers, using one memorable shared access code distributed by the author. Keep device sessions between visits; allow code revocation or rotation. Protect full prose, timing data, audio, and later artwork. |
| Publication | Proceed with the private full-book launch after implementation and technical verification. Keep the existing Vercel project and production address. |
| Narration | Both current editions are accepted as-is. No narration regeneration. Preserve the original recordings and their production files. |
| Web encoding | Use the existing **192 kbps MP3s**. This supersedes the earlier 128 kbps choice; do not re-encode the release. |
| Display credit | “Inspired by Rob Xie”. This changes the website credit, not the faithful manuscript or words already spoken in the recordings. |
| Narrator labels | “Female narrator” and “Male narrator”; start new readers with the female edition and remember their subsequent choice. |
| Images | Use generic placeholders now. Character approval is handled separately; generate story imagery only after finalization and review. |
| Devices | Optimize for mobile phones, iPad, and desktop. Retain Android installation, home-screen support, and safe-area controls. |
| Media | Prefer private Vercel Blob. Available account allowances and spending ceiling are still being confirmed. |
| Backups | Existing private Lumen Google Drive folder; preserve sharing and the existing archive. Exclude secrets, local environments, and downloaded models. |
| Secrets | Follow the machine’s dotenvx convention: encrypted owner-only ignored `.env` and `.env.shared`, with a separate owner-only ignored `.env.keys`. Load them through dotenvx from their matching directory. Server secrets stay outside browser code. |
| Later scope | Offline audiobook downloads, cross-device sync, sleep timer, personal bookmarks, search, and download products remain later features. |

Returning readers still open paused at their saved chapter and passage. Both editions need independent timing maps tied to their unchanged final MP3 hashes. Technical timing checks are distinct from the author's acceptance of narration content; no unperformed listening review should be recorded.

Engineering may complete the release while character and artwork decisions remain open. The launch presents placeholders honestly and does not expose internal casting documents, production files, credentials, or author audit notes.

Source decisions: the author explicitly stated “Audio can go as is”, “No audio regen needed”, “Start with the female voice by default”, “Prefer Blob”, “Keep them in the Lumen google drive”, and “Ok to go full launch, this is essentially a private site”; the follow-ups select existing 192 kbps files and a memorable shared access code.
