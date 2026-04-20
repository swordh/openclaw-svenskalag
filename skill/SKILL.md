---
name: svenska-lag
description: Hantera kallelser och aktiviteter för Älta IF via svlag.py CLI
---

Använd detta skill för att hjälpa användaren med sina aktivitetskallelser på Älta IF.

## Verktyg

Skript-sökväg (använd alltid exakt denna):
```
/Users/joakim.svardh/Library/CloudStorage/GoogleDrive-swordh@gmail.com/My Drive/Claude Code/Svenska lag/svlag.py
```

Kör alltid med Python och använd `--json` flaggan för maskinläsbar output:
```
python "/Users/joakim.svardh/Library/CloudStorage/GoogleDrive-swordh@gmail.com/My Drive/Claude Code/Svenska lag/svlag.py" <kommando> --json
```

## Kommandon

| Syfte | Kommando |
|-------|----------|
| Lista senaste 15 kallelser | `bokningar --json` |
| Lista alla kallelser | `bokningar --alla --json` |
| Visa vem som svarat på aktivitet | `svar <ID> --json` |
| Svara ja på kallelse | `svara <ID> ja --json` |
| Svara nej på kallelse | `svara <ID> nej -k "Anledning" --json` |
| Lista tillgängliga lag | `lag --json` |

## Tolka `bokningar` output

Varje objekt i arrayen har:
- `scheduleId` – används som `<ID>` i andra kommandon
- `status` – `"ja"` (kommer), `"nej"` (kommer ej), `"?"` (ej svarat)
- `canReply` – `true` om svar fortfarande accepteras, annars `false`

Obesvarade kallelser = `status == "?"` OCH `canReply == true`. Lyft alltid fram dessa för användaren.

## Viktiga regler

- Svara nej kräver alltid `-k "Anledning"`, annars returnerar skriptet fel
- `canReply: false` = svarstiden utgången, går ej att ändra
- Visa alltid aktivitetens namn, datum och tid när du presenterar resultat
- Om `error` finns i JSON-svaret, berätta för användaren vad som gick fel
