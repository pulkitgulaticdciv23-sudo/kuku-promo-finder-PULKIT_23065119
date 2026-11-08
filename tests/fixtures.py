"""Small fictional inputs: no Kuku screenplay text is included in these tests."""

from pathlib import Path


def screenplay_text(number=1, uppercase=False):
    heading, start, end = ("SCENE HEADING", "SCENE START", "SCENE END") if uppercase else (
        "Scene heading", "Scene start", "Scene end"
    )
    return f'''EPISODE NUMBER: Episode {number}
EPISODE LOGLINE: Two fictional characters argue.
CONTINUITY NOTES: A fictional example.

--- SCRIPT START ---

SCENE 01
{heading}: INT. ROOM – DAY
{start}: 00:00.00
{end}: 00:50.00

Actions and dialogue:

**CAMERA:**
Start: (00:00.00) | End: (00:06.00)
A wide shot of the room.

**ACTION:**
Start: (00:00.00) | End: (00:02.00)
A guard kicks open the door.

**ADA (Angry):**
Start: (00:01.00) | End: (00:03.00)
"You lied to me."

**CAMERA:**
Start: (00:02.00) | End: (00:08.00)
The camera moves closer.

**ADA (Hesitant):**
Start: (00:10.00) | End: (00:12.00)
"I thought..."

**ADA (Quiet):**
Start: (00:12.20) | End: (00:15.00)
"...you were my friend."

**CAMERA:**
Start: (00:15.00) | End: (00:20.00)
Ada looks away.

**CAMERA:**
Start: (00:20.00) | End: (00:25.00)
The light changes.

**BOB (O.S.) (Worried):**
Start: (00:24.00) | End: (00:26.00)
"Who told you?"

**CAMERA:**
Start: (00:25.00) | End: (00:30.00)
Bob enters the room.

**CAMERA:**
Start: (00:30.00) | End: (00:50.00)
The camera holds.

--- SCRIPT END ---
'''


def write_show(directory):
    root = Path(directory)
    (root / "episode-001.txt").write_text(screenplay_text(1), encoding="utf-8")
    (root / "episode-002.txt").write_text(screenplay_text(2, uppercase=True), encoding="utf-8")
    return root


def sample_candidate():
    return {
        "segments": [{"episode": 1, "start": "00:00.00", "end": "00:30.00"}],
        "hook": {"episode": 1, "timestamp": "00:01.00", "quote": '"You lied to me."'},
        "cliffhanger": "Editorial review pending.",
        "why_it_works": "Editorial review pending.",
        "context_needed": "Editorial review pending.",
    }
