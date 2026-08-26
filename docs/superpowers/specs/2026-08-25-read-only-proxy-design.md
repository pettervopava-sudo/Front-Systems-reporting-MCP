# Leseproxy for Front Systems — design

**Dato:** 2026-08-25
**Status:** godkjent design, klar for implementasjonsplan

## Problemet

Front Systems kan ikke utstede API-nøkler med kun lesetilgang. Nøklene i
`.env` (`Ocp-Apim-Subscription-Key` + `x-api-key`) gir full tilgang: den
som har dem kan i prinsippet skrive mot kassasystemet og hente kundedata.
Det gjør det ubehagelig å la Claude Code, skript eller kolleger jobbe fritt
mot API-et.

Løsningen er en **lokal leseproxy**: en liten tjeneste som hver bruker kjører
på sin egen maskin, og som fysisk ikke kan gjøre annet enn å lese. Alt
klientverktøy peker på proxyen i stedet for på Front Systems.

## Beslutninger (avklart med Petter 2026-08-25)

| Spørsmål | Valg |
|---|---|
| Hvor kjører den? | Lokalt hos **hver** bruker — ingen avhengighet til Petters maskin |
| Autentisering av brukere | Ingen egen nøkkel; proxyen binder kun til localhost |
| PII | Proxyen striper kundefelter fra alle svar |

## Arkitektur

```
Claude Code / MCP-server / fs_query / rapportskript
        │  FRONT_SYSTEMS_BASE_URL = http://127.0.0.1:8812
        ▼
   Leseproxy  (front_systems_mcp.proxy)
        │  ① kun GET   ② kun godkjente entiteter   ③ striper PII
        │  legger på de ekte nøklene fra .env
        ▼
   https://frontsystemsapis.frontsystems.no
```

Sperren er *konfigurasjon*, ikke disiplin: så lenge `BASE_URL` peker på
proxyen, finnes det ingen kodevei fra verktøyene til en skrivemetode. Å
omgå den krever en bevisst endring i `.env`.

## Komponenter

### `src/front_systems_mcp/proxy.py`

Én modul, stdlib `ThreadingHTTPServer` + `httpx` (allerede en avhengighet).
Ingen nye pakker.

**Binding.** `127.0.0.1:8812`. Porten kan settes med `--port` (om 8812 er
opptatt), men **adressen kan ikke overstyres**: modulen binder alltid til
loopback, slik at proxyen ikke ved et uhell blir en åpen lesetjeneste på
kontornettet.

**Kontrakten den håndhever:**

1. **Metode** — kun `GET`. Alt annet (`POST`, `PUT`, `PATCH`, `DELETE`,
   `HEAD`, `OPTIONS`) → `405` med en forklarende JSON-kropp. Dette er hele
   poenget med tjenesten.
2. **Sti** — kun `/odata/<Entitet>` der entiteten er i allowlisten
   `{Sales, Saleslines, Stockstatus, Stockmovements, Products}`. Alt annet
   → `404`. (`/healthz` er unntaket, se under.)
3. **Query** — sendes videre **verbatim**. `$filter`, `$select`, `$top`,
   `from`/`to`, `snapshotDateTime` røres ikke. All opparbeidet kunnskap om
   API-ets særheter (`$top` før `$filter`, 31-dagersvinduet, ikke-filtrerbare
   visningsfelter) gjelder uendret gjennom proxyen.
4. **Nøkler** — leses fra `.env` og settes på oppstrømskallet. Klientens
   egne auth-headere ignoreres og videresendes aldri.

**PII-stripping.** Svar fra `Saleslines` parses som JSON, og hver rad renses
for kundefelter før den sendes videre. Feltlisten er
`odata.PII_FIELDS` (FirstName, LastName, Email, Phone, Address, PostalCode,
City, CUSTOMERID_FK, PERSONID_FK) utvidet med de øvrige kundebærende
feltene på Saleslines: CustomerGender, CompanyName, OrgNum, IsCompany,
CountryCode, AgreedSendEmail, AgreedSendSMS, BonusBalance, BonusFactor,
BonusTotal, SaleBonusFactor.

`IsEmployee` beholdes — rapportene bruker det til å skille ansattekjøp.

Ber en klient om PII i `$select`, kommer kolonnene simpelthen ikke tilbake.
Én mekanisme, ingen egen avvisningslogikk å holde i synk. Andre entiteter
strømmes gjennom uten parsing (byte-identisk).

**Feilhåndtering.**

| Situasjon | Proxyens svar |
|---|---|
| Oppstrøms svarer 2xx/4xx/5xx | Samme status og kropp sendes gjennom urørt |
| Timeout mot Front Systems | `504` |
| Tilkoblingsfeil | `502` |
| Svar er ikke JSON på Saleslines | Sendes gjennom urørt (ingen stripping mulig) |

Ingen retry i proxyen: MCP-klienten har allerede backoff, og et ekstra lag
ville skjult feil og doblet ventetiden.

**Logging.** Én linje per kall: tidspunkt, metode, entitet, status, varighet,
antall byte. Aldri feltverdier, aldri query-innhold med kundedata, aldri
nøkler.

**`/healthz`** — svarer `200 {"ok": true}` uten å røre Front Systems, så
oppstart og «lever den?» kan verifiseres billig.

### `scripts/serve_proxy.sh`

Samme mønster som `serve_reports.sh`: dreper en eventuell kjørende instans,
starter på nytt med `nohup ... & disown`, skriver pid og port.

### Konfigurasjon

`.env` får ett nytt felt, og `BASE_URL` bytter betydning:

```
# Klientene peker på proxyen:
FRONT_SYSTEMS_BASE_URL=http://127.0.0.1:8812
# Proxyen peker på Front Systems (default hvis utelatt):
FRONT_SYSTEMS_UPSTREAM_URL=https://frontsystemsapis.frontsystems.no
FRONT_SYSTEMS_SUBSCRIPTION_KEY=...
FRONT_SYSTEMS_API_KEY=...
```

Proxyen bruker `load_config()` for nøklene og ignorerer `base_url` derfra;
oppstrøms tas fra `FRONT_SYSTEMS_UPSTREAM_URL` med kjent default.
`.env.example` oppdateres med denne oppdelingen og en forklaring.

## Oppsett for en ny bruker

1. `git clone` repoet fra GitHub
2. `cp .env.example .env` og fyll inn de to nøklene
3. `bash scripts/serve_proxy.sh`

Deretter virker MCP-serveren, `fs_query.py` og alle rapportskript uendret —
men kun lesende. Ingen maskin er avhengig av noen annen.

## Testing

Offline (fake oppstrøm, ingen nettverk):

- `POST`/`PUT`/`PATCH`/`DELETE` → 405, og oppstrøms ble aldri kalt
- ukjent entitet og sti utenfor `/odata/` → 404
- query-parametre passerer verbatim (inkl. `$top`, `from`/`to`, `$filter`)
- PII strippes fra Saleslines-rader; `IsEmployee` og tallfeltene består
- andre entiteter passerer byte-identisk
- oppstrøms 500 og tom-array-svar sendes gjennom uendret
- timeout → 504, tilkoblingsfeil → 502
- `/healthz` svarer uten å kalle oppstrøms
- binding til ikke-loopback avvises

Live (markert `live`, kjøres bevisst):

- samme éndagsspørring direkte mot Front Systems og via proxy gir likt
  radantall og like beløp
- proxy-svaret inneholder ingen av PII-nøklene

## Avgrensninger (bevisst utenfor)

- Ingen caching — rapportskriptene har sine egne cacher
- Ingen rate limiting — én bruker per proxy
- Ingen HTTPS — trafikken forlater aldri loopback
- Ingen brukeradministrasjon — se beslutningstabellen over
