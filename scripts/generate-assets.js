// Renders the ManyBet app icon (assets/logo.png) and social preview using
// sharp. Run via `npm run gen:assets`, which then calls @capacitor/assets to
// turn this single logo into every Android icon/splash resolution.
const path = require('path');
const sharp = require('sharp');

const SIZE = 1024;

const svg = `
<svg width="${SIZE}" height="${SIZE}" viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="gold" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#ffd75e"/>
      <stop offset="100%" stop-color="#c98d00"/>
    </linearGradient>
  </defs>
  <path d="M512 40 L860 168 V468 C860 690 716 866 512 984 C308 866 164 690 164 468 V168 Z"
        fill="none" stroke="url(#gold)" stroke-width="34"/>
  <path d="M512 100 L804 210 V466 C804 662 682 818 512 918 C342 818 220 662 220 466 V210 Z"
        fill="#0a0e17"/>
  <circle cx="512" cy="430" r="158" fill="none" stroke="url(#gold)" stroke-width="26"/>
  <path d="M512 300 L560 400 L470 470 L390 400 Z" fill="url(#gold)"/>
  <path d="M512 300 L470 470 L512 560 L620 470 Z" fill="url(#gold)" opacity="0.85"/>
  <path d="M512 300 L390 400 L430 500 L512 560 Z" fill="url(#gold)" opacity="0.65"/>
  <text x="512" y="700" font-family="Arial, Helvetica, sans-serif" font-size="150" font-weight="800"
        fill="url(#gold)" text-anchor="middle">IB</text>
  <text x="512" y="800" font-family="Arial, Helvetica, sans-serif" font-size="58" font-weight="700"
        letter-spacing="10" fill="#eef1f8" text-anchor="middle">MANYBET</text>
</svg>
`;

const shareSvg = `
<svg width="1200" height="630" viewBox="0 0 1200 630" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="gold" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#ffd75e"/>
      <stop offset="100%" stop-color="#c98d00"/>
    </linearGradient>
    <pattern id="lines" width="48" height="48" patternUnits="userSpaceOnUse" patternTransform="rotate(35)">
      <path d="M0 0V48" stroke="#ffffff" stroke-opacity="0.035" stroke-width="2"/>
    </pattern>
  </defs>
  <rect width="1200" height="630" fill="#0a0e17"/>
  <rect width="1200" height="630" fill="url(#lines)"/>
  <rect x="28" y="28" width="1144" height="574" rx="28" fill="none" stroke="#35404c" stroke-width="2"/>
  <path d="M846 28H1144Q1172 28 1172 56V574Q1172 602 1144 602H954L1120 436V193Z" fill="#121b24"/>
  <circle cx="235" cy="315" r="126" fill="#101722" stroke="url(#gold)" stroke-width="12"/>
  <path d="M235 205L286 300L235 404L184 300Z" fill="url(#gold)"/>
  <text x="235" y="472" font-family="Arial, Helvetica, sans-serif" font-size="66" font-weight="800" fill="url(#gold)" text-anchor="middle">MB</text>
  <text x="420" y="270" font-family="Arial, Helvetica, sans-serif" font-size="76" font-weight="800" letter-spacing="2" fill="#f5f7fb">MANY<tspan fill="url(#gold)">BET</tspan></text>
  <text x="422" y="330" font-family="Arial, Helvetica, sans-serif" font-size="28" fill="#c4ccd6">Pronostics sportifs du campus</text>
  <path d="M422 374H718" stroke="url(#gold)" stroke-width="4"/>
  <text x="422" y="426" font-family="Arial, Helvetica, sans-serif" font-size="21" font-weight="700" letter-spacing="3" fill="#e2e8f0">MATCHS  ·  PRONOSTICS  ·  CLASSEMENT</text>
</svg>
`;

async function main() {
  const outDir = path.join(__dirname, '..', 'assets');
  await sharp(Buffer.from(svg)).resize(SIZE, SIZE).png().toFile(path.join(outDir, 'logo.png'));
  const shareDir = path.join(__dirname, '..', 'static', 'uploads-app');
  await sharp(Buffer.from(shareSvg)).png().toFile(path.join(shareDir, 'manybet-share.png'));
  console.log('ManyBet app logo and social preview generated.');
}

main().catch((err) => { console.error(err); process.exit(1); });
