/**
 * Frida script to extract player positions from a running LoL replay on Windows.
 *
 * Approach: Hook memory writes to detect when PathPacket data is decoded.
 * After decryption, the game writes entity_id (u32) and position floats to memory.
 * We intercept these writes to capture positions.
 *
 * Usage:
 *   1. Launch a replay from the LoL client
 *   2. Run: frida -p <PID of League of Legends.exe> -l frida_positions_win.js
 *   3. Or: frida -n "League of Legends.exe" -l frida_positions_win.js
 *
 * The script outputs JSON lines to stdout with player positions.
 */

'use strict';

// Player entity IDs (10 players)
const PLAYER_ENTITY_START = 0x400000AE;
const PLAYER_ENTITY_END = 0x400000B7;

// Summoner's Rift bounds
const MAP_MIN = 0;
const MAP_MAX = 15000;

// Track positions
const positions = {};
let gameTime = 0;

// Find the base address of the game module
const gameModule = Process.findModuleByName("League of Legends.exe");
if (!gameModule) {
    console.log("ERROR: Could not find League of Legends.exe module");
} else {
    console.log(`[*] Found module at ${gameModule.base}, size ${gameModule.size}`);

    // Strategy 1: Scan for the lookup table in .rdata to find decrypt functions
    // The 255-byte lookup table starts with specific bytes
    // We'll scan for it and then find xrefs

    // Strategy 2: Hook the PathPacket parsing function
    // After decryption, PathPacket::parse reads:
    //   u16 parsing_type, u32 entity_id, f32 speed, then waypoints
    // We can find this by searching for the waypoint formula constants:
    //   7358.0 (0x45E5E000 as float) and 7412.0 (0x45E7A000 as float)

    const MAGIC_X = 7358.0;
    const MAGIC_Y = 7412.0;

    // Scan for these float constants in .rdata
    const magicXBuf = new ArrayBuffer(4);
    new Float32Array(magicXBuf)[0] = MAGIC_X;
    const magicXBytes = new Uint8Array(magicXBuf);

    console.log(`[*] Searching for waypoint formula constants...`);

    // Search in the module's memory
    const ranges = gameModule.enumerateRanges('r--');
    let foundCount = 0;

    for (const range of ranges) {
        try {
            const pattern = '00 E0 E5 45';  // 7358.0 as LE float
            const matches = Memory.scanSync(range.base, range.size, pattern);
            for (const match of matches) {
                // Check if 7412.0 is nearby (within 8 bytes)
                const nearby = match.address.add(4).readFloat();
                if (Math.abs(nearby - MAGIC_Y) < 1.0) {
                    console.log(`[*] Found waypoint constants at ${match.address}`);
                    foundCount++;
                }
            }
        } catch(e) {}
    }

    console.log(`[*] Found ${foundCount} waypoint constant pairs`);

    // Strategy 3: More robust - scan for float writes in the valid position range
    // Hook common memory allocation and watch for PathPacket-like structures

    // The simplest approach: periodically scan known memory regions for entity positions
    // After the game processes packets, entity positions are stored in game objects

    // Search for entity objects in memory
    // Each entity object has its netId (0x400000ae-0x400000b7) stored somewhere
    // followed by position floats

    console.log(`[*] Scanning for player entity objects...`);

    function scanForEntities() {
        const heap = Process.enumerateRanges('rw-');
        const results = [];

        for (const range of heap) {
            if (range.size > 100 * 1024 * 1024) continue; // skip very large ranges
            if (range.size < 1024) continue;

            try {
                for (let entityId = PLAYER_ENTITY_START; entityId <= PLAYER_ENTITY_END; entityId++) {
                    const pattern = (entityId & 0xFF).toString(16).padStart(2, '0') + ' ' +
                                   ((entityId >> 8) & 0xFF).toString(16).padStart(2, '0') + ' ' +
                                   ((entityId >> 16) & 0xFF).toString(16).padStart(2, '0') + ' ' +
                                   ((entityId >> 24) & 0xFF).toString(16).padStart(2, '0');

                    const matches = Memory.scanSync(range.base, Math.min(range.size, 10 * 1024 * 1024), pattern);

                    for (const match of matches) {
                        // Check surrounding memory for position-like floats
                        for (const offset of [4, 8, 12, 16, 20, 24, 28, 32, 48, 64, 80, 96, 112, 128]) {
                            try {
                                const x = match.address.add(offset).readFloat();
                                const y = match.address.add(offset + 4).readFloat();

                                if (x > MAP_MIN && x < MAP_MAX && y > MAP_MIN && y < MAP_MAX) {
                                    results.push({
                                        entityId: entityId,
                                        playerIndex: entityId - PLAYER_ENTITY_START,
                                        address: match.address.toString(),
                                        posOffset: offset,
                                        x: Math.round(x * 10) / 10,
                                        y: Math.round(y * 10) / 10,
                                    });
                                }
                            } catch(e) {}
                        }
                    }
                }
            } catch(e) {}
        }

        return results;
    }

    // Run a scan
    const entities = scanForEntities();
    console.log(`[*] Found ${entities.length} potential entity positions`);
    for (const e of entities.slice(0, 20)) {
        console.log(`  Player ${e.playerIndex}: (${e.x}, ${e.y}) at ${e.address}+${e.posOffset}`);
    }

    // If we found stable addresses, we can poll them
    if (entities.length > 0) {
        // Group by playerIndex, find most common offset
        const byPlayer = {};
        for (const e of entities) {
            if (!byPlayer[e.playerIndex]) byPlayer[e.playerIndex] = [];
            byPlayer[e.playerIndex].push(e);
        }

        console.log(`\n[*] Setting up position polling...`);

        // Pick the first match for each player and poll every 100ms
        const trackedAddresses = {};
        for (const [idx, ents] of Object.entries(byPlayer)) {
            if (ents.length > 0) {
                trackedAddresses[idx] = {
                    address: ptr(ents[0].address),
                    offset: ents[0].posOffset,
                };
            }
        }

        setInterval(function() {
            const snapshot = { time: Date.now(), players: {} };
            for (const [idx, info] of Object.entries(trackedAddresses)) {
                try {
                    const x = info.address.add(info.offset).readFloat();
                    const y = info.address.add(info.offset + 4).readFloat();
                    if (x > MAP_MIN && x < MAP_MAX && y > MAP_MIN && y < MAP_MAX) {
                        snapshot.players[idx] = {
                            x: Math.round(x * 10) / 10,
                            y: Math.round(y * 10) / 10,
                        };
                    }
                } catch(e) {}
            }
            if (Object.keys(snapshot.players).length > 0) {
                send(snapshot);
            }
        }, 500);  // Every 500ms
    }
}
