/* Fond animé — braises.
   ==========================================================================
   Un shader écrit à la main plutôt qu'une bibliothèque 3D : le décor d'une
   page d'accueil ne justifie pas six cents kilo-octets de dépendance, et la
   règle du site est de ne rien charger depuis l'extérieur.

   Le dessin repose sur une déformation de domaine : un bruit fractal sert à
   déplacer les coordonnées d'un second bruit, puis d'un troisième. C'est ce
   qui donne ces volutes de chaleur, là où un simple dégradé animé ne produit
   que des vagues régulières et vite lassantes.

   Trois garde-fous, parce qu'un fond ne doit jamais coûter à la page :
   le rendu s'arrête dès qu'il sort de l'écran ou que l'onglet passe en
   arrière-plan, la résolution est plafonnée, et si le système demande de
   réduire les animations on peint une seule image fixe.
   ========================================================================== */

(() => {
  const toile = document.getElementById("braises");
  if (!toile) return;

  const gl = toile.getContext("webgl", {
    alpha: false, antialias: false, depth: false, stencil: false,
    powerPreference: "low-power",
    failIfMajorPerformanceCaveat: false,
  });
  /* Sans WebGL — vieux navigateur, accélération désactivée — la page garde
     les halos CSS, qui restent affichés tant qu'on n'a pas pris la main. */
  if (!gl) return;

  const SOMMET = `
    attribute vec2 position;
    void main() { gl_Position = vec4(position, 0.0, 1.0); }
  `;

  const FRAGMENT = `
    precision mediump float;
    uniform vec2  u_taille;
    uniform float u_temps;
    uniform vec2  u_souris;     // 0..1, amortie côté JavaScript

    float alea(vec2 p) {
      return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
    }

    float bruit(vec2 p) {
      vec2 i = floor(p), f = fract(p);
      vec2 u = f * f * (3.0 - 2.0 * f);          // lissage cubique
      return mix(mix(alea(i),                alea(i + vec2(1.0, 0.0)), u.x),
                 mix(alea(i + vec2(0.0,1.0)), alea(i + vec2(1.0, 1.0)), u.x), u.y);
    }

    float fractal(vec2 p) {
      float v = 0.0, a = 0.5;
      for (int i = 0; i < 5; i++) {
        v += a * bruit(p);
        p = p * 2.03 + 13.7;                     // décalage : évite l'alignement
        a *= 0.5;
      }
      return v;
    }

    void main() {
      vec2 uv = gl_FragCoord.xy / u_taille.xy;
      // Repère isotrope : sans cela, les volutes s'étirent sur un écran large.
      vec2 p = (gl_FragCoord.xy - 0.5 * u_taille.xy) / min(u_taille.x, u_taille.y);
      p *= 2.6;

      float t = u_temps * 0.055;

      // Déformation de domaine, en deux passes. C'est là que naissent les
      // volutes : chaque passe déplace la suivante.
      vec2 q = vec2(fractal(p + vec2(0.0, t)),
                    fractal(p + vec2(5.2, 1.3) - vec2(0.0, t * 0.8)));
      vec2 r = vec2(fractal(p + 3.4 * q + vec2(1.7, 9.2) + t * 0.4),
                    fractal(p + 3.4 * q + vec2(8.3, 2.8) - t * 0.3));
      float f = fractal(p + 3.2 * r);

      // La chaleur monte : on penche le champ vers le haut de l'image.
      f = f * 0.72 + (1.0 - uv.y) * 0.34;
      // Le curseur attise une zone, faiblement : un décor ne doit pas happer
      // le regard au point de détourner de la lecture.
      float d = distance(uv * vec2(u_taille.x / u_taille.y, 1.0),
                         u_souris * vec2(u_taille.x / u_taille.y, 1.0));
      f += 0.16 * exp(-d * 4.2);

      // Palette : encre profonde, brun de braise, orange, or.
      vec3 encre  = vec3(0.031, 0.035, 0.047);
      vec3 braise = vec3(0.204, 0.086, 0.043);
      vec3 feu    = vec3(0.847, 0.333, 0.106);
      vec3 or     = vec3(1.000, 0.729, 0.376);

      vec3 couleur = encre;
      couleur = mix(couleur, braise, smoothstep(0.34, 0.72, f));
      couleur = mix(couleur, feu,    smoothstep(0.66, 0.94, f) * 0.50);
      couleur = mix(couleur, or,     smoothstep(0.88, 1.04, f) * 0.30);

      // Vignette : ramène l'œil au centre et protège la lisibilité des bords.
      float vignette = smoothstep(1.32, 0.28, length(uv - 0.5) * 1.42);
      couleur *= 0.34 + 0.66 * vignette;

      // Un grain très fin, qui casse les bandes de dégradé des écrans 8 bits.
      couleur += (alea(gl_FragCoord.xy + u_temps) - 0.5) * 0.016;

      gl_FragColor = vec4(couleur, 1.0);
    }
  `;

  function compiler(type, source) {
    const nuanceur = gl.createShader(type);
    gl.shaderSource(nuanceur, source);
    gl.compileShader(nuanceur);
    if (!gl.getShaderParameter(nuanceur, gl.COMPILE_STATUS)) {
      console.warn("Braises — compilation :", gl.getShaderInfoLog(nuanceur));
      gl.deleteShader(nuanceur);
      return null;
    }
    return nuanceur;
  }

  const sommet = compiler(gl.VERTEX_SHADER, SOMMET);
  const fragment = compiler(gl.FRAGMENT_SHADER, FRAGMENT);
  if (!sommet || !fragment) return;

  const programme = gl.createProgram();
  gl.attachShader(programme, sommet);
  gl.attachShader(programme, fragment);
  gl.linkProgram(programme);
  if (!gl.getProgramParameter(programme, gl.LINK_STATUS)) {
    console.warn("Braises — édition de liens :", gl.getProgramInfoLog(programme));
    return;
  }
  gl.useProgram(programme);

  // Deux triangles couvrant l'écran.
  const tampon = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, tampon);
  gl.bufferData(gl.ARRAY_BUFFER,
                new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const position = gl.getAttribLocation(programme, "position");
  gl.enableVertexAttribArray(position);
  gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);

  const uTaille = gl.getUniformLocation(programme, "u_taille");
  const uTemps = gl.getUniformLocation(programme, "u_temps");
  const uSouris = gl.getUniformLocation(programme, "u_souris");

  const sobre = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  /* La résolution est plafonnée : ce champ est flou par nature, le rendre en
     3× sur un écran dense coûterait neuf fois plus de pixels pour un gain
     invisible. */
  const DENSITE = Math.min(window.devicePixelRatio || 1, 1.5);
  const ECHELLE = 0.72;

  let largeur = 0, hauteur = 0;
  function redimensionner() {
    const l = Math.max(1, Math.round(toile.clientWidth * DENSITE * ECHELLE));
    const h = Math.max(1, Math.round(toile.clientHeight * DENSITE * ECHELLE));
    if (l === largeur && h === hauteur) return;
    largeur = toile.width = l;
    hauteur = toile.height = h;
    gl.viewport(0, 0, largeur, hauteur);
    gl.uniform2f(uTaille, largeur, hauteur);
  }

  let sourisCible = [0.5, 0.34];
  let souris = [0.5, 0.34];
  if (!sobre && window.matchMedia("(pointer: fine)").matches) {
    window.addEventListener("pointermove", (e) => {
      sourisCible = [e.clientX / window.innerWidth,
                     1.0 - e.clientY / window.innerHeight];
    }, { passive: true });
  }

  let debut = performance.now();
  let image = 0;
  let visible = true;

  function peindre(instant) {
    redimensionner();
    // Amortissement : la lumière suit la main sans la coller.
    souris[0] += (sourisCible[0] - souris[0]) * 0.045;
    souris[1] += (sourisCible[1] - souris[1]) * 0.045;
    gl.uniform2f(uSouris, souris[0], souris[1]);
    gl.uniform1f(uTemps, (instant - debut) / 1000);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  function boucle(instant) {
    if (!visible) { image = 0; return; }
    peindre(instant);
    image = requestAnimationFrame(boucle);
  }

  function demarrer() {
    if (image || !visible) return;
    debut = performance.now() - 8000;   // on entre dans un champ déjà formé
    image = requestAnimationFrame(boucle);
  }

  function arreter() {
    cancelAnimationFrame(image);
    image = 0;
  }

  document.documentElement.classList.add("braises-actives");

  if (sobre) {
    redimensionner();
    peindre(performance.now() + 8000);   // une seule image, figée
    return;
  }

  /* On ne calcule rien quand le fond n'est pas à l'écran : passé la première
     section, ce sont des images par seconde rendues pour personne. */
  if ("IntersectionObserver" in window) {
    const oeil = new IntersectionObserver((entrees) => {
      visible = entrees.some((e) => e.isIntersecting);
      if (visible) demarrer(); else arreter();
    }, { rootMargin: "120px" });
    oeil.observe(toile);
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) arreter();
    else if (visible) demarrer();
  });

  window.addEventListener("resize", redimensionner, { passive: true });

  /* Le feu appartient à l'accroche. Laissé à pleine intensité sur toute la
     page, il concurrence chaque section, nuit à la lecture, et cesse de faire
     de l'effet — un éblouissement permanent n'est plus un éblouissement. Il
     retombe donc à l'état de braise dès qu'on descend. */
  const RESIDU = 0.14;
  let opacite = -1;
  function doser() {
    const course = Math.max(1, window.innerHeight * 0.9);
    const part = Math.min(1, window.scrollY / course);
    const valeur = Math.round((1 - (1 - RESIDU) * part) * 100) / 100;
    if (valeur !== opacite) {
      opacite = valeur;
      toile.style.opacity = String(valeur);
    }
  }
  doser();
  window.addEventListener("scroll", doser, { passive: true });

  demarrer();
})();
