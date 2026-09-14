/* La porte du site publié.
   ==========================================================================

   Ce que ce fichier peut faire, et ce qu'il ne peut pas : voir le commentaire
   en tête de `templates/site/_porte.html`. En deux mots — le site est un jeu
   de fichiers servis tels quels, sans serveur pour vérifier quoi que ce soit.
   La porte masque le contenu, elle ne l'enferme pas.

   Le code lui-même n'est écrit nulle part. La page porte son empreinte : deux
   cent mille tours de PBKDF2 sur un sel tiré au hasard à la publication.
   Retrouver le code depuis l'empreinte demande de les essayer un par un, et
   chaque essai coûte les deux cent mille tours — ce qui ne protège pas d'une
   machine patiente, mais suffit à ce qu'un coup d'œil à la source ne suffise
   pas.

   Le déverrouillage instantané d'un visiteur déjà entré n'est pas ici : il
   est en ligne, dans la page, pour qu'il ait lieu avant le premier affichage.
   Ce fichier ne s'occupe que du formulaire. */
(() => {
  "use strict";

  const racine = document.documentElement;
  const porte = document.getElementById("porte");
  if (!porte || !racine.classList.contains("verrouille")) return;

  const forme = document.getElementById("porte-forme");
  const champ = document.getElementById("porte-code");
  const bouton = document.getElementById("porte-bouton");
  const avis = document.getElementById("porte-avis");

  const SEL = porte.dataset.sel;
  const ATTENDU = porte.dataset.empreinte;
  const TOURS = parseInt(porte.dataset.tours, 10);
  const MEMOIRE = "flambee-porte";

  /* `crypto.subtle` n'existe qu'en contexte sûr — https, ou localhost. Sur
     une page servie en http clair, il est absent, et la porte resterait
     fermée sans jamais dire pourquoi. On le dit. */
  const possible = window.crypto && window.crypto.subtle;

  const enHexa = (tampon) =>
    [...new Uint8Array(tampon)].map((o) => o.toString(16).padStart(2, "0")).join("");

  const enOctets = (hexa) =>
    Uint8Array.from(hexa.match(/../g).map((h) => parseInt(h, 16)));

  async function empreinte(code) {
    const encodeur = new TextEncoder();
    const base = await crypto.subtle.importKey(
      "raw", encodeur.encode(code), "PBKDF2", false, ["deriveBits"]);
    const bits = await crypto.subtle.deriveBits(
      { name: "PBKDF2", salt: enOctets(SEL), iterations: TOURS, hash: "SHA-256" },
      base, 256);
    return enHexa(bits);
  }

  function ouvrir() {
    racine.classList.remove("verrouille");
    porte.remove();
  }

  function refuser(message) {
    avis.textContent = message;
    porte.classList.add("refuse");
    champ.select();
    /* L'animation est retirée dès qu'elle est finie : laissée en place, elle
       ne repartirait pas au refus suivant, et le deuxième essai raté n'aurait
       aucun retour visible. */
    setTimeout(() => porte.classList.remove("refuse"), 500);
  }

  forme.addEventListener("submit", async (e) => {
    e.preventDefault();
    /* Normalisé comme à la publication : sans espaces autour, en
       minuscules. Un téléphone met une majuscule à la première lettre
       tout seul, et le code serait refusé pour cette seule raison. */
    const code = champ.value.trim().toLowerCase();
    if (!code) return;

    if (!possible) {
      refuser("Ce navigateur ne peut pas vérifier le code ici. "
              + "Ouvre le site en https.");
      return;
    }

    bouton.disabled = true;
    avis.textContent = "";
    /* Le calcul prend une fraction de seconde — c'est voulu, c'est ce qui
       rend les essais coûteux — mais assez pour qu'un bouton muet donne
       l'impression que rien ne se passe. */
    const attente = setTimeout(() => { bouton.textContent = "Vérification…"; }, 120);

    try {
      if (await empreinte(code) === ATTENDU) {
        try { localStorage.setItem(MEMOIRE, ATTENDU); } catch (err) { /* refusé */ }
        ouvrir();
        return;
      }
      refuser("Ce code ne correspond pas.");
    } catch (err) {
      refuser("La vérification a échoué sur ce navigateur.");
    } finally {
      clearTimeout(attente);
      bouton.disabled = false;
      bouton.textContent = "Entrer";
    }
  });

  champ.focus({ preventScroll: true });
})();
