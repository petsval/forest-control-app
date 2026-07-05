# Metsatöö kontroll v10

Selles versioonis on:

- harvesteri PRD import
- kontori tööjaotus langi kaupa: kes ja millise masinaga võib langil vedada
- vedukamehe lihtne veokoguse sisestus
- sortimendid PRD failist sama nimega
- vedukande juures veo pikkus ja raskusaste
- langi koond ja langi detail sortimentide kaupa
- kõikide lankide üldkogus
- vedukamehe parandused koos logiga
- lisatööde sisestus sama langi peale
- lisatööde raport ja CSV eksport tellijale
- Excellenti CSV ekspordis ka veo pikkuse keskmine ja raskusastmed

## Käivitamine Windowsis

1. Paki ZIP lahti.
2. Ava kaust `forest_control_app_v10_veopikkus_raskus`.
3. Tee topeltklikk `run_windows.bat` failil.
4. Must aken peab jääma lahti.
5. Ava brauseris:

http://127.0.0.1:8010

Vedukamehe otsevaade:

http://127.0.0.1:8010/vedukamees

## Vedukamehe veokanne

Vedukamees sisestab:

- juht
- lank
- sortiment
- vahetus
- veetud kogus tm
- koormate arv
- veo pikkus meetrites
- raskusaste: tavapärane, pehme, mägine või väga raske
- märkus

Harvesteri kogust vedukamehele ei näidata.

## Kontori ülevaade

Kontor näeb:

- lankide koondis keskmist veo pikkust ja raskusastmeid
- langi detailis kõiki sama langi vedusid koos veo pikkuse ja raskusastmega
- kanded vaates iga sisestuse täpseid andmeid
- Excellenti ekspordis veo pikkuse keskmist ja raskusastmeid sortimendi real

## Lisatöö sisestus

Vedukamehe lehel on eraldi lisatöö osa: lisatöö nimetus, tunnid ja märkus. Kontor näeb lisatöid menüüst `Lisatööd` ja saab CSV alla laadida.

## V11: masinate ja juhtide register

Uues versioonis on menüüs **Masinad ja juhid**.

Seal saab kontor lisada:
- harvesterid ja vedukid;
- masina nime ja seerianumbri;
- kas masin on oma või alltöövõtu masin;
- alltöövõtja nime;
- juhid, sh alltöövõtu juhid;
- millised juhid tohivad millise masinaga töötada.

Tööjaotuse lehel saab seejärel määrata langi peale konkreetse vedukamehe ja masina. Vedukamees näeb sisestuses ainult talle määratud lanke.
