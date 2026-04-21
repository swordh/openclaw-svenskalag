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
| Visa detaljer + svarslista för aktivitet | `svar <ID> --json` |
| Svara ja på kallelse | `svara <ID> ja --json` |
| Svara nej på kallelse | `svara <ID> nej -k "Anledning" --json` |
| Lista tillgängliga lag | `lag --json` |

## Tolka `bokningar` output

Varje objekt i arrayen har:

**Tid & identitet**
- `scheduleId` – används som `<ID>` i andra kommandon (kan vara null för aktiviteter utan länk)
- `date` – datum som svensk sträng, t.ex. `"Tor 23 apr"`
- `startTime` – starttid, t.ex. `"19:00"`
- `endTime` – sluttid, t.ex. `"20:30"` (hämtas från iCal-feed, tillgänglig för alla aktiviteter)
- `name` – aktivitetens namn, t.ex. `"Träning"` eller `"Match"`
- `type` – `"träning"`, `"match"` eller `"övrigt"`

**Plats & personal**
- `location` – plan eller arena, t.ex. `"Stavsborgs BP"` eller `"Älta IP 1"`
- `trainers` – lista med tränare/ledare: `[{"name": "Calle Hedenström", "role": "Lagledare/Tränare"}, ...]`
- `meetingPoint` – samlingstid, t.ex. `"19:50"`

**Svarsstatus**
- `status` – `"ja"` (kommer), `"nej"` (kommer ej), `"?"` (ej svarat)
- `canReply` – `true` om svar fortfarande accepteras, annars `false`
- `deadline` – sista svarstid, t.ex. `"23 apr, 18:50"`

**Antal svar** (hämtas från detaljsidan)
- `attendingCount` – antal som kommer, t.ex. `"14+1"`
- `notAttendingCount` – antal som inte kommer
- `invitedCount` – antal kallade som ej svarat
- `totalInvited` – totalt antal kallade

Obesvarade kallelser = `status == "?"` OCH `canReply == true`. Lyft alltid fram dessa för användaren.

## Tolka `svar` output

Utöver fälten ovan innehåller `svar`-svaret:
- `attending` – lista med namn på spelare som kommer
- `notAttending` – lista med `{"name": "...", "comment": "..."}` för de som tackat nej
- `unanswered` – lista med namn på spelare som inte svarat
- `opponent` – motståndare (matcher), t.ex. `"Reymersholms IK 1"`
- `homeAway` – `"hemma"` eller `"borta"` (matcher)
- `result` – matchresultat om tillgängligt

Tränare/ledare visas **inte** i spelarlistan — de är separerade i `trainers`-fältet.

## Viktiga regler

- Svara nej kräver alltid `-k "Anledning"`, annars returnerar skriptet fel
- `canReply: false` = svarstiden utgången, går ej att ändra
- Visa alltid aktivitetens namn, datum, start- och sluttid samt plan när du presenterar resultat
- Om `error` finns i JSON-svaret, berätta för användaren vad som gick fel
- `endTime` och `location` kan vara tomma för aktiviteter utan länk och som saknas i iCal-feeden
