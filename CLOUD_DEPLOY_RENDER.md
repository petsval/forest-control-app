# Pilve paigaldusjuhis Render.com jaoks

See versioon on mõeldud nii, et vedukamees saab metsas mobiilse internetiga avada oma personaalse lingi ja kontor saab parooliga sisse logida.

## 1. Tee GitHubi repository

1. Tee GitHubis uus private repository, näiteks `metsaveo-kontroll`.
2. Laadi selle ZIP-i sisu sinna üles.

## 2. Tee Renderis uus Web Service

1. Ava Render.
2. Vali **New +** -> **Web Service**.
3. Ühenda GitHubi repository.
4. Seaded:
   - Environment: `Python`
   - Build command: `pip install -r requirements.txt`
   - Start command: `python app.py`

## 3. Lisa keskkonnamuutujad

Renderi Web Service seadetest lisa:

- `ADMIN_PASSWORD` = kontori tugev parool
- `DATA_DIR` = `/var/data`

## 4. Lisa püsiv ketas

Renderis lisa teenusele Disk:

- Mount path: `/var/data`
- Size: 1 GB või rohkem

See on tähtis, sest SQLite andmebaas peab pilves alles jääma.

## 5. Ava aadress

Render annab aadressi kujul:

`https://metsaveo-kontroll.onrender.com`

Kontor avab selle aadressi ja logib parooliga sisse.

Vedukamehele saada link lehelt **Lingid**.

## 6. Oluline

- Kontori vaated on parooliga kaitstud.
- Vedukamehe personaalsed lingid on lihtsad sisestuslingid.
- Kui tahad tugevamat turvalisust, järgmine samm on vedukamehe PIN-kood või SMS/telefonipõhine sisselogimine.
