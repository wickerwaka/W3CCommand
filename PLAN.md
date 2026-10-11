# W3CCommand
This project is a port of the 16-bit game Carrier Command to a modern web application. It uses the original Atari ST version of the game as the source for a decompilation effort and then rebuilds the core gameplay logic and rendering so it can run in a browser on a desktop or mobile device.

## Goals
- Game fully playable in a modern browser on desktop or mobile
- Maintains flat colorful polygonal graphics, updated UI look
- Instant state saving and restoring to either local storage or cloud storage

## Phases

### Decompilation and Extraction
Decompile the ST version of the game using ghidra and other tools. World state, simulation logic, enemy AI, procedural world generation and UI all need to be extracted in a state that will allow recreating in a modern language, either rebuilt in javascript/typescript or using a webassembly runtime. Extract images, 3D models and audio.

### Verification
Run the decompiled world simulation alongside a version of the game running in an emulator and ensure the world state stays in sync. Be able to extract world state from the emulated game and insert it into the recreation, continue playing and do the opposite.

### Web Build
Rebuild the game as a web application, probably using threejs or a similar rendering library.

### Modernize
Update the UI. Increase 3D fidelity. Improve audio. Add touch controls.

