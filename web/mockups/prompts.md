# Lumen reader style mockups

Final comparison files: `cinematic-painted-v2.png`, `ink-graphic-novel-v1.png`, `photographic-thriller-v1.png`, and `editorial-collage-v2.png`. Earlier variants are preserved.

Generated with the built-in imagegen tool for visual comparison. These are static design concepts; the character appearance and interface are provisional.

All variants show the opening of Chapter 1: Adrian in his modest Berkeley craftsman bungalow, cold coffee nearby, watching helicopter footage of a glass tower on television. He is shown from behind to leave his final appearance open. Each reader includes chapter navigation, prose with a narration highlight, and audio controls.

## Shared generation prompt

Use case: ui-mockup.
Create one polished, high fidelity desktop web interface mockup for LUMEN, an illustrated audiobook reader. Output a landscape 16:10 image, a straight-on flat screen design filling the canvas, without laptop hardware, browser address bar, perspective, or a surrounding presentation board.
All mockups use the same opening of Chapter 1. Functional structure: quiet top bar with LUMEN and "by Rob Xie", a "Chapters" control, "Read & listen" active, and "Aa". Main content is a generous story image next to a readable prose column. A narrow bottom playback bar has -15, Pause, +15, a progress track, "00:18 / 11:23", and "1×". The narrative image and prose should be equally intentional; the prose must sit on a clean surface, never over busy artwork.
Story image: a modest, lived-in Berkeley craftsman bungalow on Monday evening. Adrian Marsh, only seen from the back or as a three-quarter silhouette, sits quietly watching a television. A ceramic mug of cold coffee is near him. Television shows helicopter footage of a contemporary glass office tower in San Francisco. Keep the setting grounded and human. Do not define Adrian's full face, ethnicity, or age. No other people, no futuristic technology, no weapons, no story spoilers.
Prose column text (verbatim, clear professional serif typography):
"Chapter 1"
"The crawl at the bottom of the screen said BREAKING, which in the taxonomy of modern cable news meant anything from a nuclear detonation to a celebrity's new haircut."
"Adrian Marsh reached for his coffee, already cold, and watched the helicopter footage circle a glass tower in San Francisco's South of Market district like a vulture that had spotted something interesting."
"Adrian set the mug down. It was Monday evening."
Use a restrained highlight on the second paragraph to suggest narration focus. Show "PART I" as a small contextual label. No made-up quotes, slogans, extra chapter titles, audio waveforms, gamification, marketing calls to action, or dashboards. Preserve readable text and generous margins. This is a reader screen, not a book cover or landing page.

## Cinematic painted realism

Art treatment: sophisticated cinematic painted realism, softly brushed surfaces, rich charcoal shadows, subtle amber practical light contrasting with cool television glow, textured but precise architecture, quiet restrained atmosphere. One expansive image, no comic panels. Interface: warm charcoal background, ivory literary serif text, delicate muted gold narration highlight, very subtle separators, clean flat restrained controls. Scene image takes roughly half the width; prose stays highly readable. LUMEN uses an elegant serif wordmark. The image should feel hand-painted rather than photographic.

## Ink graphic novel

Art treatment: mature literary graphic novel, strong black contours, confident crosshatching, carved shadow shapes, limited cream/grey ink with a single deep oxblood red accent. One large living-room panel with two discreet inset panels showing the coffee mug and the television tower. No speech bubbles. Interface: warm cream paper surface, black typography, thin decisive panel rules, bold editorial LUMEN lettering, compact dark red active cue. Retain the same image-and-prose split and playback functions, but make the visual language unmistakably ink illustration.

## Photographic thriller

Art treatment: photorealistic cinematic political thriller film still, natural wood and cloth texture, subtle realistic film grain, low evening light, cool slate shadows and restrained amber practical light. Adrian seen from behind, television in focus, authentic modest Berkeley craftsman room. No painterly strokes, no comic lines. Interface: deep midnight blue and dark graphite, warm white serif prose, clean understated sans-serif controls, a restrained bronze narration marker. A broad cinematic image and a calm dark prose column, spacious and immersive. No glossy sci-fi effects.

## Minimalist editorial collage

Art treatment: sophisticated minimalist editorial cut-paper collage, angular charcoal silhouettes, cropped monochrome architectural photograph on the television, paper grain, a subtle coffee-ring texture and muted terracotta accent. Suggest the same living-room story through a few meaningful fragments: seated rear silhouette, lit TV with tower, mug, window and craftsman wood structure. Avoid portraits. Interface: luminous warm ivory, large literary black typography, fine rules, generous negative space, precise asymmetrical editorial composition, restrained terracotta listening cue. Art and readable book prose have equal importance. The result should feel like a contemporary literary magazine, not an empty wireframe.

## Refinement prompts

cinematic-painted:

Use case: ui-mockup, style-transfer refinement. Edit the supplied LUMEN reader concept. Keep the entire web interface, all text, serif typography, layout, proportions, audio controls, dark charcoal/ivory/gold colors, and exact Chapter 1 excerpt unchanged. Change only the story artwork on the left so it is unmistakably a beautiful HAND-PAINTED CINEMATIC ILLUSTRATION: visible confident gouache/oil brushwork, broad simplified color planes, soft lost edges, expressive painterly shadows, rich muted amber and slate light. Preserve the same modest Berkeley craftsman room, Adrian shown only from behind, cold coffee mug, and television showing a glass office tower. The illustration must not look like a photograph or a photo filter. No new characters, no story clues. Remove specific TV station logos from the scene; use a small neutral BREAKING label only. The UI itself must stay sharp, flat, and highly legible.

editorial-collage:

Use case: ui-mockup, style-transfer refinement. Edit the supplied LUMEN reader concept. Keep ALL existing web interface text, chapter excerpt, top navigation, audio controls, ivory/black/terracotta colors, and the clean readable right text column unchanged. Completely replace the left story artwork with unmistakably MINIMALIST EDITORIAL CUT-PAPER COLLAGE. Reduce the room to a few flat angular paper shapes on an airy ivory background: charcoal rear silhouette of a seated reader, simplified wood window frame, a small ceramic mug with a coffee-ring cutout, and a rectangle television containing a cropped monochrome glass tower photograph. Large areas of ivory negative space, tactile torn-paper edges, deliberately limited terracotta, muted brown and charcoal color blocks; sophisticated literary magazine art direction. No realistic detailed room, no photographic person, no painterly full scene, no vintage poster lettering. Keep the same scene meaning, contemporary Berkeley evening, person watching television, cold coffee. Preserve sharp UI and exact prose; no extra words, people, clues, or TV station logos.
