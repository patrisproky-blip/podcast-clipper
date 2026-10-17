# Podcast Clipper

Aplikace, která z dlouhých YouTube podcastů automaticky vyrábí krátká svislá videa (9:16, s titulky) pro TikTok, Shorts a Reels. Hotový program se spouští dvojklikem, **nepotřebujete Python ani nic instalovat**.

Tato složka obsahuje zdrojový kód. Samotný program (Windows a Mac) se z něj sestaví automaticky zdarma na GitHubu. Postup je níže, vyžaduje jen klikání v prohlížeči.

---

## Část 1: Získání programu (jednorázově, asi 10 minut práce a 30 minut čekání)

1. **Účet na GitHubu.** Na github.com se zaregistrujte (zdarma).
2. **Nový repozitář.** Vpravo nahoře `+` → *New repository*. Název např. `podcast-clipper`, zvolte **Public** (veřejný, díky tomu je sestavování bez limitu a hotový program se dá stáhnout bez přihlášení) a potvrďte *Create repository*. Žádná tajná data v něm nejsou, API klíč se do něj nikdy nedostane.
3. **Nahrání souborů.** Rozbalte tento zip. V novém repozitáři klikněte na *uploading an existing file* a **přetáhněte do okna všechno, co je uvnitř rozbalené složky** (soubory i složky `ui` a `assets`). Dole klikněte *Commit changes*.
4. **Soubor se sestavením.** GitHub potřebuje soubor `.github/workflows/build.yml`. Složky s tečkou jsou na Macu skryté, proto ho vytvořte ručně:
   - v repozitáři *Add file* → *Create new file*
   - do pole s názvem napište přesně `.github/workflows/build.yml` (lomítka vytvoří složky)
   - otevřete v textovém editoru soubor `build-workflow.yml` z tohoto zipu, zkopírujte celý obsah a vložte ho
   - *Commit changes*
   
   (Pokud už po kroku 3 existuje složka `.github`, tento krok přeskočte.)
5. **Čekání.** Sestavení se spustí samo. V záložce *Actions* uvidíte běh se žlutým kolečkem, po 20–40 minutách se změní na zelenou fajfku.
6. **Stažení.** Na hlavní stránce repozitáře vpravo klikněte na *Releases* → nejnovější položka. Dole stáhněte `PodcastClipper-Windows.zip` nebo `PodcastClipper-macOS.zip` a rozbalte.

**Ještě jednodušší cesta:** v nastavení Claude (claude.ai → Settings → Connectors) propojte svůj GitHub, vytvořte prázdný veřejný repozitář (kroky 1–2) a napište mi jeho název. Soubory nahraju a sestavení spustím já, vám zbyde jen krok 6.

**Červený křížek u běhu?** Klikněte na něj, rozklikněte červený krok a pošlete mi text chyby nebo snímek obrazovky. Sestavení obsahuje samotest, který hlásí, co v programu chybí.

Poznámka: sestavení pro Mac je pro počítače s čipem Apple (M1 a novější).

## Část 2: První spuštění

**Windows:** rozbalte zip a spusťte `PodcastClipper.exe`. Windows může zobrazit modré okno „Počítač byl chráněn“, protože program nemá placený podpis. Klikněte na *Další informace* → *Přesto spustit*.

**Mac:** rozbalte zip a přetáhněte `PodcastClipper.app` do Aplikací. Při prvním spuštění macOS program zablokuje. Otevřete *Nastavení systému* → *Soukromí a zabezpečení*, dole klikněte *Přesto otevřít*. Pokud hlásí „je poškozená“, napište v Terminálu `xattr -dr com.apple.quarantine /Applications/PodcastClipper.app`.

Po spuštění se otevře prohlížeč s aplikací. Pak:

1. V záložce **Nastavení** vložte API klíč Claude (vytvoříte ho v Anthropic Console, console.anthropic.com).
2. Vložte odkaz na YouTube video nahoře a klikněte *Vytvořit klipy*.
3. První přepis stáhne jazykový model (u výchozí „small“ asi 500 MB), proto první video trvá déle.

## Jak se používá

- **Klipy:** hotová videa s přehrávačem, názvem, hookem a skóre. Jdou stáhnout.
- **Fronta:** průběh zpracování. Při chybě je tlačítko *Zkusit znovu*.
- **Kanály:** přidáte kanál podcastu a aplikace sama zpracuje každý nový díl (musí běžet).
- **Nastavení:** jazyk, přesnost přepisu, počet a délka klipů, vzhled titulků.

Zavřením okna prohlížeče aplikace neskončí, běží dál na pozadí. Vypnete ji tlačítkem *Ukončit aplikaci* dole.

Vaše data (nastavení vč. API klíče, rozpracovaná videa a hotové klipy) jsou ve složce `PodcastClipper` ve vaší domovské složce. Hotové klipy jsou v podsložce `Klipy`.

## Tipy a omezení

- Bez grafické karty zvolte přesnost „small“ nebo „medium“. „large-v3“ je velmi pomalý.
- Rozpoznání obličeje jen vystředí obraz na mluvčího, nepozná, kdo zrovna mluví. U dvou lidí vedle sebe se použije rozdělená obrazovka.
- YouTube občas blokuje stahování. Když stahování selže, zkuste to později. YouTube se mění často, může být potřeba program znovu sestavit s novější verzí `yt-dlp` (v záložce *Actions* stačí znovu spustit *Run workflow*).
- Klipy z cizích podcastů vyžadují svolení autora.
- Výběr momentů je placený podle spotřeby u Anthropic. Za jednu epizodu to bývají jednotky až desítky korun.

## Pro vývojáře

Spuštění ze zdrojů: `pip install -r requirements.txt` a `python app.py` (potřebuje nainstalovaný `ffmpeg`). Sestavení lokálně: `pip install pyinstaller imageio-ffmpeg` a `python build.py`. Kontrola sestaveného programu: `PodcastClipper --selftest`.
