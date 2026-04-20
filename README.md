# openclaw-svenskalag

OpenClaw skill för att hantera kallelser och aktiviteter på [Älta IF](https://www.altaif.se) via kommandoraden – eller direkt i en AI-agent.

## Vad gör det?

`svlag.py` loggar in på Älta IFs hemsida och låter dig:

- Lista dina kallelser (träningar, matcher m.m.)
- Se vem som svarat på en aktivitet
- Svara ja eller nej på en kallelse
- Lista tillgängliga lag på klubben

OpenClaw-skillen gör samma saker tillgängliga för en AI-agent som kan förstå och presentera datan på naturligt språk.

---

## Installation

### 1. Klona och installera beroenden

```bash
git clone https://github.com/swordh/openclaw-svenskalag.git
cd openclaw-svenskalag
pip install -r requirements.txt
```

### 2. Konfigurera miljövariabler

```bash
cp .env.example .env
```

Fyll i `.env` med dina uppgifter:

```
ALTAIF_USER=Förnamn Efternamn
ALTAIF_PASS=ditt_lösenord
ALTAIF_DOMAIN=www.altaif.se
ALTAIF_TEAMS=altaif-fotboll-fotbolldamera
```

Hitta rätt lag-slug med: `python svlag.py lag`

### 3. Installera OpenClaw-skillen

Kopiera skill-mappen till OpenClaws skill-katalog och uppdatera sökvägen i `SKILL.md`:

```bash
cp -r skill ~/.openclaw/skills/svenska-lag
```

Öppna `~/.openclaw/skills/svenska-lag/SKILL.md` och byt ut sökvägen i instruktionerna mot den faktiska sökvägen till `svlag.py` på din maskin.

Starta sedan en ny OpenClaw-session:

```
/new
```

---

## Användning – CLI

```bash
# Lista dina senaste 15 kallelser
python svlag.py bokningar

# Lista alla kallelser
python svlag.py bokningar --alla

# Visa vem som svarat på aktivitet 12345
python svlag.py svar 12345

# Svara ja
python svlag.py svara 12345 ja

# Svara nej (kommentar krävs)
python svlag.py svara 12345 nej -k "Bortrest"

# Lista lag-slugs
python svlag.py lag
```

Alla kommandon stöder `--json` för maskinläsbar output:

```bash
python svlag.py bokningar --json
```

---

## Användning – OpenClaw

När skillen är installerad kan du fråga agenten på naturligt språk:

> "Vilka kallelser har jag den här veckan?"  
> "Svara ja på träningen på onsdag"  
> "Har alla svarat på söndagens match?"

Agenten kör `svlag.py --json` i bakgrunden och presenterar resultatet.

---

## Filstruktur

```
svlag.py          # CLI-verktyget
requirements.txt  # Python-beroenden
.env.example      # Mall för miljövariabler
skill/
  SKILL.md        # OpenClaw skill-definition
```

## Krav

- Python 3.10+
- Ett konto på altaif.se
- [OpenClaw](https://openclaw.ai) (för agent-användning)
