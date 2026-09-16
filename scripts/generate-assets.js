// Renders the IUT Bet app icon (assets/logo.png) from a hand-drawn SVG using
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
        letter-spacing="10" fill="#eef1f8" text-anchor="middle">IUT BET</text>
</svg>
`;

async function main() {
  const outDir = path.join(__dirname, '..', 'assets');
  await sharp(Buffer.from(svg)).resize(SIZE, SIZE).png().toFile(path.join(outDir, 'logo.png'));
  console.log('assets/logo.png generated.');
}

main().catch((err) => { console.error(err); process.exit(1); });
