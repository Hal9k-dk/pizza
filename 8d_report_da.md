# 8D-rapport: Hændelse i pizza-bestillingsautomatisering
## Fejl udløst efter ændring af URL-konfiguration
**Format:** Slide deck med speaker notes  
**Tone:** Formelt, professionelt og let undskyldende

---

## Slide 1 — Titel-slide

**8D-rapport for korrigerende handlinger**  
**Emne:** Fejl i automatiseret pizza-bestillingsflow efter URL-opdatering  
**System:** `place-orders` Playwright-automatisering  
**Dato:** 2026-04-02

**Speaker notes:**  
Tak for muligheden for at undersøge denne hændelse grundigt. Vi beklager, at automatiseringen fejlede under reel brug, særligt fordi det afbrød en ellers rutinemæssig bestillingsproces for en anden bruger. Denne rapport opsummerer hændelsen efter 8D-metoden, herunder umiddelbar afhjælpning, teknisk root cause, korrigerende handlinger, validering og forebyggende tiltag.

---

## Slide 2 — Executive Summary

### Resumé
- Selve opdateringen af Google Sheet var **ikke** den fejlede komponent.
- Den faktiske fejl opstod i **navigationen på pizzasitet og valget af ordretype**.
- En ændring i URL-konfigurationen blotlagde en svaghed i, hvordan `place-orders` byggede menu-URL'en.
- Der fandtes desuden en separat fejl i, hvordan scriptet interagerede med valget **“Udbringning eller Afhentning”**.
- Automatiseringen er nu rettet og gjort mere robust.

### Forretningsmæssig påvirkning
- Bestillingsprocessen blev afbrudt.
- En bruger så et crash i stedet for en fejl, der kunne forstås eller håndteres.
- Tilliden til automatiseringen blev midlertidigt reduceret.

**Speaker notes:**  
Selvom fejlen først blev sat i forbindelse med opdateringen af regnearket, viste undersøgelsen, at ordreudtrækket fungerede korrekt, og at det egentlige sammenbrud opstod senere, da browser-automatiseringen interagerede med pizzasitet. Kort sagt nåede systemet frem til menusitet, men dets antagelser om URL-håndtering og dialogen for valg af bestillingstype var for skrøbelige. Vi har nu rettet begge svagheder og forbedret valideringen ved opstart.

---

## Slide 3 — D1: Etabler teamet

### Team / roller
- **Undersøgelsesansvarlig:** Coding assistant / debugger
- **Indberetter:** Bruger, som leverede `crash.txt`
- **Reproduktionskontekst:** En anden bruger, som kørte scriptet med opdateret konfiguration
- **Involverede systemkomponenter:**
  - `extract_orders.py`
  - `place_orders.py`
  - `.env` / `PIZZA_PLACE`
  - Playwright browser-automatisering
  - Eksternt site: `skalborgpizza.dk`

**Speaker notes:**  
Undersøgelsen byggede primært på crash-loggen, gennemgang af kildekode, live-interaktion med målsitet samt kontrolleret reproduktion med Playwright mod det aktuelle produktionssite. Problemet omfattede både intern kode og ekstern website-adfærd, så debug-arbejdet dækkede konfigurationshåndtering, DOM-interaktion, navigationstiming og robusthed i browser-automatiseringen.

---

## Slide 4 — D2: Problembeskrivelse

### Problemformulering
Når `place-orders` blev kørt efter en URL-opdatering, gjorde scriptet følgende:
1. Hentede og viste dagens ordrer fra Google Sheets korrekt,
2. Åbnede pizzasitet,
3. Fejlede under valg af ordretype / interaktion med menuen,
4. Gav et crash i stedet for at fortsætte til kurv-opbygning.

### Evidens fra `crash.txt`
- Ordrer blev udtrukket korrekt.
- Fejlen opstod ved:
  - `page.evaluate("changemenucard(2)")`
- Undtagelse:
  - `ReferenceError: changemenucard is not defined`
- Efterfølgende cleanup-fejl:
  - `RuntimeError: This event loop is already running`

### Observerbart brugersymptom
- Ved manuel kørsel så det ud til, at scriptet hang ved:
  - **“Udbringning eller Afhentning”**

**Speaker notes:**  
Det vigtigste spor var, at sheet-data blev indlæst korrekt, og at alle 14 ordrer blev vist før fejlen opstod. Det udelukkede regnearket som primært fejlområde. Fejlen opstod først, da Playwright-flowet forsøgte at interagere med pizzasitet. Den efterfølgende event-loop-fejl var sekundær og opstod under oprydning efter den første exception.

---

## Slide 5 — D2: Problemets omfang og afgrænsning

### Hvad var påvirket
- Det automatiserede placeringsflow i `place_orders.py`
- Brugere, der er afhængige af browser-automatisering til ordreindtastning

### Hvad var ikke påvirket
- Google Sheet-ekstraktionslogik
- Parsing af den opdaterede sheet-URL
- Visning af ordrer og ordresammendrag i terminalen

### Betingelser for at udløse fejlen
- Opdateret eller ikke-root `PIZZA_PLACE`-URL
- Eksekveringssti, der går gennem valg af afhentning/udbringning
- Timing-følsom DOM-transition efter valg af **Afhentning**

**Speaker notes:**  
Dette var ikke en total systemfejl. Ekstraktionspipelinen fungerede, og problemet var isoleret til browser-automatiseringslaget. Mere specifikt blev fejlen udløst af en kombination af konfigurationsfølsomhed og skrøbelige antagelser om strukturen og timingen på målsitet.

---

## Slide 6 — D3: Midlertidige afhjælpende handlinger

### Umiddelbar afhjælpning
- Gennemgik `crash.txt` for at isolere det første fejlkald.
- Verificerede live site-adfærd med Playwright.
- Bekræftede at:
  - `changemenucard()` findes på den rigtige menyside,
  - men ikke er pålidelig på alle afledte stier,
  - og at popup-overlayet blokerer klik på linket på den underliggende side.
- Implementerede kodeændringer for at:
  - normalisere den konfigurerede menu-URL,
  - klikke på den faktiske **popup-knap** for `Afhentning`,
  - vente til menuen igen er fuldt interaktiv.

### Midlertidig risikoreduktion
- Tilføjede tydeligere validering af forventet menyside-struktur.
- Opdaterede README med vejledning om gyldige `PIZZA_PLACE`-formater.

**Speaker notes:**  
Afhjælpningen fokuserede på at gøre scriptet operationelt igen så hurtigt og sikkert som muligt, samtidig med at det tilsigtede automatiseringsflow blev bevaret. Vi valgte bevidst målrettede kodeændringer frem for brede omskrivninger, så genopretningen kunne være både hurtig og teknisk forsvarlig.

---

## Slide 7 — D4: Root Cause Analysis

## 4A. Direkte årsag

### Direkte teknisk årsag
Scriptet antog, at det altid sikkert kunne gøre begge dele:
- udlede menu-URL'en ved at tilføje `/menukort` til hvad som helst, `PIZZA_PLACE` indeholdt,
- udløse valg af afhentning ved at eksekvere eller klikke på kontrolen `changemenucard(2)` på siden.

Disse antagelser var ikke universelt gyldige.

### Fejlmekanisme
- Hvis `PIZZA_PLACE` ikke var sitets root eller den direkte menu-URL, kunne scriptet bygge en forkert sti som f.eks.:
  - `/menu/menukort`
  - `/bestil/menukort`
- På sådanne sider var den forventede JavaScript-funktion eller struktur ikke pålidelig.
- Selv på den korrekte side, når popup'en for **ordretype** var åben, opsnappede overlayet klik til linket på den underliggende side.
- Efter valg af afhentning gik sitet kortvarigt ind i en mellemtilstand i DOM'en, og scriptet ventede ikke på, at menuen blev interaktiv igen.

**Speaker notes:**  
Fejlen skyldtes ikke én enkelt tastefejl eller én enkelt bug. Det var en kæde af antagelser: først om URL-konstruktion, derefter om hvilket element der skulle klikkes på, og til sidst om timing efter popup-handlingen. Tilsammen gjorde disse antagelser scriptet skrøbeligt.

---

## Slide 8 — D4: Root Cause Analysis

## 4B. Root cause og escape point

### Root cause
**Automatiseringslogikken var for afhængig af implicitte antagelser om website-struktur og timing i stedet for at validere sidens tilstand og interagere med den aktive modal-kontrol.**

### Medvirkende årsager
1. **Skrøbelig konfiguration**
   - `PIZZA_PLACE` blev ikke normaliseret robust.
2. **Forkert interaktionsmål**
   - Scriptet ramte linket på siden i stedet for den aktive popup-knap.
3. **Utilstrækkelig readiness-waiting**
   - Koden antog, at “overlay skjult” betød “menu klar”, hvilket ikke var korrekt.
4. **Svag validering ved opstart**
   - Scriptet fejlede ikke tidligt med en klar besked, når sidestrukturen var uventet.

### Escape point
- Der fandtes ingen automatiseret test for:
  - ikke-root `PIZZA_PLACE`-værdier,
  - popup-baseret valg af afhentning,
  - transitionel DOM-adfærd efter valg af `Afhentning`.

**Speaker notes:**  
Kvalitetsmæssigt var root cause ikke kun selve defekten, men også fraværet af de værn, der burde have opdaget den tidligere. Vi burde have valideret de acceptable URL-former og eksplicit testet popup-flowet. Det hul gjorde, at problemet slap igennem til reel brug.

---

## Slide 9 — D5: Valgte permanente korrigerende handlinger

### Valgte korrigerende handlinger
1. **Normalisering af pizza-site-URL'er**
   - Tving konfigurerede site-URL'er til at pege på den kanoniske sti `/menukort`.
2. **Valg af afhentning via aktiv popup-kontrol**
   - Brug `#colorbox input[value='Afhentning']`, når den findes.
3. **Eksplicit vent på, at menuen er klar**
   - Ny readiness-check bekræfter, at:
     - menu-items findes,
     - `showItemDetails` er tilgængelig,
     - siden reelt er interaktiv.
4. **Forbedret sidevalidering**
   - Fejl hurtigt, hvis siden ikke matcher den forventede legacy-menustruktur.
5. **Oprydning i linting / Ruff-fejl**
   - Forbedr kodekvalitet og vedligeholdbarhed for at reducere skjulte fejl.

**Speaker notes:**  
De valgte handlinger havde til formål både at løse det akutte problem og den bagvedliggende strukturelle svaghed. Vi undgik bevidst kun at lappe det observerede symptom. I stedet rettede vi URL-håndtering, modal-interaktion og synkroniseringslogik som et samlet sæt.

---

## Slide 10 — D6: Implementer og valider korrigerende handlinger

### Implementerede ændringer
**`place_orders.py`**
- Tilføjede `normalize_menu_url()`
- Tilføjede `choose_pickup(page)`
- Tilføjede `wait_for_menu_ready(page)`
- Erstattede den skrøbelige logik for valg af afhentning
- Tilføjede tydeligere sidevalidering

**`README.md`**
- Præciserede accepteret brug af `PIZZA_PLACE`

**`extract_orders.py` og `pyproject.toml`**
- Ryddede op i Ruff-fejl og konfigurationsadvarsler

### Udført validering
- Bekræftede, at ordrer fortsat udtrækkes korrekt
- Bekræftede, at popup-knappen kan klikkes på live-sitet
- Bekræftede, at popup og overlay forsvinder korrekt
- Bekræftede, at menu-items vender tilbage efter den transitionelle reload
- Bekræftede, at `ruff` passerer
- Bekræftede, at koden er committed og pushed korrekt

**Speaker notes:**  
Valideringen blev udført mod live-adfærden på websitet og ikke kun mod kildekoden. Det var vigtigt, fordi problemet afhang af DOM-struktur og asynkrone sideovergange. Vi ryddede også op i lint-fejlene for at efterlade kodebasen i en mere kontrolleret tilstand efter rettelsen.

---

## Slide 11 — D6: Valideringsresultater

### Centrale valideringsobservationer
- Før valg af afhentning:
  - menusiden indeholdt de forventede ordre-items
- Under valg af afhentning:
  - popup-knappen udløste de nødvendige backend-kald
- Umiddelbart efter klik:
  - siden gik kortvarigt ind i en ufuldstændig / mellemtilstand
- Efter readiness-wait:
  - menu-item-listen vendte tilbage i fuld tilgængelighed
  - popup'en var væk
  - overlayet var væk

### Resultat
- Automatiseringen kan nu gå stabilt gennem:
  - site load,
  - valg af afhentning,
  - readiness af menu,
  - interaktion med items

**Speaker notes:**  
Et af de mest værdifulde fund under valideringen var kollapset i DOM'en lige efter valg af `Afhentning`. Det forklarede, hvorfor en simpel “klik og fortsæt”-tilgang var upålidelig. Den korrigerede logik venter nu på, at sitet vender tilbage til en brugbar tilstand, før der skrabes eller tilføjes varer.

---

## Slide 12 — D7: Forebyg gentagelse

### Forebyggende handlinger
1. **Hærdning af konfiguration**
   - Behandl `PIZZA_PLACE` som en basis-host og ikke som et vilkårligt deep link
2. **Tilstandsbaseret automatisering**
   - Foretræk aktive modal-kontroller frem for skjulte eller dækkede elementer på siden
3. **Readiness-checks**
   - Vent på funktionel sidetilstand, ikke kun på visuelle overgange
4. **Bedre startdiagnostik**
   - Fejl med specifikke og handlingsrettede beskeder
5. **Anbefalet regressionsdækning**
   - Tilføj tests eller smoke checks for:
     - root-URL,
     - direkte `/menukort`-URL,
     - popup-flow for valg af afhentning,
     - readiness efter valget

### Yderligere anbefaling
- Tilføj en lille `.env.example` og eventuelt en startup-self-test for sanity-check af konfiguration.

**Speaker notes:**  
For at forhindre gentagelse er den vigtigste læring, at browser-automatisering skal være tilstandsbaseret og ikke antagelsesbaseret. Eksterne websites ændrer sig ofte subtilt, og robust automatisering skal verificere, hvad der faktisk er til stede og klikbart ved runtime.

---

## Slide 13 — D8: Lukning og anerkendelse

### Lukningsstatement
Sagen er nu:
- undersøgt,
- reproduceret,
- rettet,
- valideret,
- dokumenteret.

### Anerkendelse
Der skal rettes tak for:
- crash-capture i `crash.txt`,
- brugerens hint om, at scriptet hang ved **“Udbringning eller Afhentning”**,
- den hurtige opfølgning, som bekræftede, at problemet fortsatte efter den første rettelse.

**Speaker notes:**  
Vi sætter pris på den klare feedback og det ekstra hint, som blev givet under debug-forløbet. I denne sag var brugerobservationen om, at scriptet sad fast ved valget mellem afhentning og udbringning, særligt værdifuld og førte direkte til den korrekte endelige diagnose.

---

## Slide 14 — Konklusion

### Endelig konklusion
Denne hændelse udsprang af skrøbelige automatiseringsantagelser, som blev blotlagt af en konfigurationsændring og et modal-drevet website-flow. Opdateringen af regnearket var kun samtidig og ikke årsagen; den reelle fejl lå i URL-normalisering, popup-interaktion og utilstrækkelig venten på, at menuen blev klar.

### Status
- Korrigerende rettelse implementeret
- Kodekvalitetsproblemer adresseret
- Ændringer committed og pushed
- Risikoen for gentagelse væsentligt reduceret

### Afsluttende note
Vi beklager den forstyrrelse, denne fejl medførte. Selvom problemet nu er forstået og rettet, burde det have været opdaget tidligere gennem mere defensiv validering og mere flow-specifik testning. Disse mangler er nu tydeligt identificeret, og koden er forbedret derefter.

**Speaker notes:**  
Afslutningsvis beklager vi den ulejlighed, denne fejl forårsagede i en reel bestillingssituation. Undersøgelsen er afsluttet, de bagvedliggende årsager er forstået, og rettelserne er på plads. Lige så vigtigt er det, at de forebyggende tiltag i denne rapport bør gøre automatiseringen mere robust fremadrettet.

---

# Appendix (valgfrit slide-materiale)

## Teknisk root cause i én sætning
Scriptet antog fejlagtigt, at enhver konfigureret pizza-URL sikkert kunne omskrives til en fungerende menu-side, og at valg af afhentning kunne udløses fra den underliggende side, mens det i praksis var nødvendigt at håndtere den aktive popup direkte og genvalidere menuen efter en transient reload.

## Commit-reference
- `f6a35e5` — `Fix ordering flow and clean up Ruff issues`
