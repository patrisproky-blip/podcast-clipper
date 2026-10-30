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

Poznámka: sestavuje se jen verze pro Windows.

## Část 2: První spuštění

**Windows:** rozbalte zip a spusťte `PodcastClipper.exe`. Windows může zobrazit modré okno „Počítač byl chráněn“, protože program nemá placený podpis. Klikněte na *Další informace* → *Přesto spustit*.

**Mac:** rozbalte zip a přetáhněte `PodcastClipper.app` do Aplikací. Při prvním spuštění macOS program zablokuje. Otevřete *Nastavení systému* → *Soukromí a zabezpečení*, dole klikněte *Přesto otevřít*. Pokud hlásí „je poškozená“, napište v Terminálu `xattr -dr com.apple.quarantine /Applications/PodcastClipper.app`.

Po spuštění se otevře prohlížeč s aplikací. Pak:

1. Vložte odkaz na YouTube video nahoře a klikněte *Vytvořit klipy*.
2. Aplikace video stáhne a přepíše řeč (první přepis stáhne jazykový model, u výchozí „small“ asi 500 MB, proto první video trvá déle).
3. Ve **Frontě** se úloha změní na *Čeká na vás*. Klikněte na *Kopírovat pokyny*, vložte je do chatu na claude.ai (stačí bezplatný účet), odpověď od Claude zkopírujte do pole pod pokyny a klikněte *Vytvořit klipy*. U dlouhých dílů je pokynů více, každé patří do nového chatu.
4. Aplikace ukáže vybrané klipy ke kontrole. Můžete upravit začátek a konec, změnit název, klip vyřadit a pustit si ukázku. Pak kliknete na *Vytvořit klipy*.

Výběr momentů přes chat je zdarma, ale vyžaduje vaši krátkou práci u každého videa. Sledování celého kanálu proto u každého dílu zastaví v *Čeká na vás*. Chcete-li, aby šlo vše samo, přepněte v *Nastavení* na automatický výběr. Ten potřebuje API klíč z Anthropic Console (console.anthropic.com) s předplaceným kreditem.

## Jak se používá

- **Klipy:** hotová videa s přehrávačem, názvem, hookem a skóre. Jdou stáhnout.
- **Fronta:** průběh zpracování a výběr momentů přes chat. Při chybě je tlačítko *Zkusit znovu*.
- **To nebo to:** vyrobí od nuly svislá videa se hrou „To nebo to“ (nahoře modrá, dole červená možnost s obrázkem, namluvená otázka českým hlasem, odpočet 3, 2, 1, procenta pro zábavu a výzva do komentářů). Hlas (Microsoft Edge TTS) a loga značek (podle domény, z veřejné služby ikon webů) a obrázky (Noto Emoji, licence Apache 2.0) se stahují z internetu, bez nich video vznikne jen s textem. Zadáte jedno nebo více témat (jedno na řádek, pro hromadnou výrobu), zkopírujete pokyny do chatu na claude.ai, odpověď vložíte zpět a aplikace videa vytvoří. Hotová videa najdete v záložce Klipy.
- **Kanály:** přidáte kanál podcastu a aplikace sama zpracuje každý nový díl (musí běžet).
- **Nastavení:** jazyk, přesnost přepisu, typ hledaných klipů (podcast, hra „To nebo to“, záznam streamu), rozložení videa (podcast nebo hra nahoře a kamera dole), počet a délka klipů, styl titulků (zvýrazněné slovo, tmavý podklad, bez zvýraznění, jedno velké slovo) a zdroj fotek pro videa „To nebo to“ (obrázky z internetu bez klíče, Pixabay nebo bez fotek).

Zavřením okna prohlížeče aplikace neskončí, běží dál na pozadí. Vypnete ji tlačítkem *Ukončit aplikaci* dole.

Vaše data (nastavení vč. API klíče, rozpracovaná videa a hotové klipy) jsou ve složce `PodcastClipper` ve vaší domovské složce. Hotové klipy jsou v podsložce `Klipy`.

## Tipy a omezení

- Přepis běží na procesoru. Výchozí „automaticky“ vybere nejpřesnější model, který se podle vašeho procesoru a délky videa stihne v časovém limitu (výchozí 45 minut), a po prvním přepisu si změří skutečnou rychlost. První použití většího modelu stáhne soubor o velikosti asi 1 až 2 GB.
- Z hlasitosti zvuku se označují místa se smíchem, výkřiky a důrazem, což Claude pomáhá najít zajímavé momenty.
- Klipy se snaží začínat a končit na hranici věty a hlasitost se u každého klipu vyrovnává. Stahování se při výpadku opakuje.
- Obličeje hledá přesnější detektor YuNet (když se při sestavení stáhne, jinak starší záložní detektor).
- Rozpoznání obličeje jen vystředí obraz na mluvčího, nepozná, kdo zrovna mluví. U dvou lidí vedle sebe se použije rozdělená obrazovka.
- YouTube občas blokuje stahování. Když stahování selže, zkuste to později. YouTube se mění často, může být potřeba program znovu sestavit s novější verzí `yt-dlp` (v záložce *Actions* stačí znovu spustit *Run workflow*).
- Klipy z cizích podcastů vyžadují svolení autora.
- Ruční výběr přes chat je zdarma (podléhá limitům bezplatného chatu). Automatický výběr přes API je placený podle spotřeby.

## Pro vývojáře

Spuštění ze zdrojů: `pip install -r requirements.txt` a `python app.py` (potřebuje nainstalovaný `ffmpeg`). Sestavení lokálně: `pip install pyinstaller imageio-ffmpeg` a `python build.py`. Kontrola sestaveného programu: `PodcastClipper --selftest`.


## Novinky
- Videa „To nebo to“ mají hudbu na pozadí (vlastní smyčka, ztiší se pod hlasem; jde vypnout v Nastavení) a procenta se rozjedou od nuly.
- Nové rozložení podcastu „jeden velký záběr, kamera sleduje mluvčího“: aplikace pozná, kdo zrovna mluví, podle pohybu úst a kameru na něj přepne.
