# Lumen visual continuity

The reader uses cinematic painted realism: grounded present-day spaces, visible brushwork, charcoal shadows, warm practical light and cool screen light. This directory holds production references for a consistent cast across the book.

[cast-bible.json](cast-bible.json) records all 18 people in the v6 narration pronunciation list, Raven, and four supporting faces: Santos, Lin, Jonas and Maya. Every verified appearance, age, build or wardrobe fact points to the current v6 manuscript or revision canon with a one-based source line. Canon facts and provisional visual choices are separate.

## Current model sheets

Two reference sheets have been generated for the pilot. They are **provisional v1 casting choices**, ready for review; they are not described as author-approved.

- [Adrian Marsh](models/adrian-marsh-v1.png): front, three-quarter, profile, rear, full-body front/back and expression studies. His glasses are established in the manuscript. The thin frames, apparent age, complexion, dark wavy hair, light stubble and charcoal sweater are visual choices matched to the pilot.
- [Daniel Yoon](models/daniel-yoon-v1.png): the same views plus neutral, faint-smile and concern studies. Preserve the face from the existing television painting. His age of twenty-six, East Asian appearance, slight build and worn badge are manuscript facts; the hairstyle, facial proportions and navy jacket are visual choices.

These sheets are image references to include in future generation calls. A repeated character name or text prompt alone does not define a stable face. Use the sheet pixels, current costume variant and relevant location image together, then compare the result with neighboring scenes.

All other principal and supporting model sheets are **planned and not generated**. Their `models/<id>-v1.png` paths reserve an identity slot; they do not imply that a file exists. The JSON records reference status and SHA-256 for the two existing sheets.

## Canon and casting

Use `Draft/v6/revision_canon.json` for explicit revision decisions, the v6 part files for scene and appearance facts, and `Draft/v6/narration-guide.txt` for spelling and production aliases. Older planning documents do not override revised canon.

Do not fill a missing fact by treating a surname, accent, location or planning archetype as an appearance specification. Choose the missing visual detail explicitly, record it under `provisional_art_choices`, and retain that choice across scenes once reviewed. Portraits represent original fictional actors.

The full principal registry is Adrian, Daniel, Seb, Kai, Nadia, Raven, Ólafur, Tomás, Ines, Andrei, Sophie, Priya, Morrow, Polk, Strutt, Venn, Cole, Laine and Barrington. A new minor-character illustration gets its own record before generation so it cannot accidentally borrow a principal face.

## Timeline and wardrobe

Choose the variant for the actual scene rather than repeatedly using one costume throughout the book.

- Kai’s conference photograph depicts him at **twenty-nine**. Current-day Kai is **thirty-six**. Use the same actor with a distinct younger photo reference.
- The original Daniel and the later substitute are separate actors with matching build and approximate resemblance. The substitute sheet remains planned.
- Raven’s later bruises, sling and guarded movement belong to later scenes. Her neutral base sheet must not contain those injuries.
- The Cartographer role moves between Priya and Adrian; actor identity remains separate from operational role.
- Adrian keeps his face, hair and glasses as his posture becomes more decisive later. A change in emotional state does not create a new actor.

## Opening location

Adrian’s house is a modest Berkeley craftsman bungalow. The active pilot establishes dark timber trim and furniture, book-filled shelves, a textile sofa, ceramic mug, plants and a low television cabinet. Those materials and positions are provisional visual choices to preserve between shots. Warm lamps, blue evening windows and television glare define the lighting.

Maintain the opening-room camera and furniture layout when the broadcast changes. Television footage is a scene within the room, so its cooler light and broadcast composition can differ from the painted domestic setting while retaining the same art treatment. NovaMind is a present-day glass tower in South of Market; no invented futuristic hardware is needed.

## Reveal timing

This is a book-wide **production registry**, not reader runtime data. Keep it and the named model sheets outside `web/dist`. Runtime data contains only the scene cues and spoiler-safe labels for the current chapter.

The Chapter 1 suspect remains **unnamed** in captions, alt text, thumbnails and employee-badge text. Narration first names Daniel in Chapter 2; the public name release occurs in Chapter 3. Chapter boundaries alone are insufficient when a new detail arrives midway through a chapter: reveal it at its actual narration/text anchor.

Keep Kai’s earlier presentation ambiguous until the Chapter 86 confession. Avoid invented emblems, costume hints or image groupings that disclose hidden alliances. A model sheet may use later text to establish stable anatomy, but future injuries, allegiances, outcomes and timeline variants do not belong in earlier scenes.

Aliases from the narration guide are production vocabulary. Entries without a verified public reveal chapter remain internal until a scene cue confirms that the listener knows them.

## Review before use

Check the generated scene against its model sheet for face geometry, hairline, complexion, glasses and proportions. Check the scene against the manuscript for clothing, props, location and reveal timing. Check adjacent scenes for identity and room continuity. Record an intentional actor change as a new model version and preserve the previous version.
