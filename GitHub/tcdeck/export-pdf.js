const puppeteer = require('puppeteer');
const { PDFDocument } = require('pdf-lib');
const fs = require('fs');
const path = require('path');

(async () => {
  const browser = await puppeteer.launch({
    headless: true,
    args: ['--allow-file-access-from-files', '--no-sandbox']
  });
  const page = await browser.newPage();

  // Set viewport BEFORE loading for correct layout
  await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 3 });

  const filePath = path.resolve(__dirname, 'TimeCopilot Deck.html');
  await page.goto('file://' + filePath, { waitUntil: 'domcontentloaded', timeout: 60000 });

  // Inject CSS to disable ALL animations and force everything visible
  await page.addStyleTag({
    content: `
      *, *::before, *::after {
        animation: none !important;
        transition: none !important;
      }
      .animate-in, .delay-1, .delay-2, .delay-3, .delay-4, .delay-5, .delay-6 {
        opacity: 1 !important;
        transform: none !important;
      }
      .nav-controls, a[download] {
        display: none !important;
      }
      .mouse-spotlight {
        display: none !important;
      }
      :root {
        --slide-padding: clamp(0.7rem, 1.4vw, 1.4rem) !important;
      }
      .slide {
        transform: scale(1) !important;
      }
      .slide.active .content {
        transform: scale(1.15);
        transform-origin: center center;
      }
    `
  });

  // Wait for images to load
  await page.evaluate(() => {
    return Promise.all(
      Array.from(document.images)
        .filter(img => !img.complete)
        .map(img => new Promise((resolve) => {
          img.onload = resolve;
          img.onerror = resolve;
        }))
    );
  });
  // Extra wait for any stragglers
  await new Promise(r => setTimeout(r, 3000));

  const total = await page.evaluate(() => document.querySelectorAll('.slide').length);
  console.log(`Found ${total} slides`);

  const pdfDoc = await PDFDocument.create();

  for (let i = 1; i <= total; i++) {
    await page.evaluate((n) => {
      const active = document.querySelector('.slide.active');
      if (active) active.classList.remove('active');
      const slide = document.querySelector(`.slide[data-slide="${n}"]`);
      slide.classList.add('active');
    }, i);

    // Wait for rendering
    await new Promise(r => setTimeout(r, 300));

    const screenshot = await page.screenshot({ type: 'png', fullPage: false });
    console.log(`Captured slide ${i}/${total}`);

    const image = await pdfDoc.embedPng(screenshot);
    const pageWidth = 1920;
    const pageHeight = 1080;
    const pdfPage = pdfDoc.addPage([pageWidth, pageHeight]);
    pdfPage.drawImage(image, {
      x: 0, y: 0,
      width: pageWidth,
      height: pageHeight,
    });
  }

  const pdfBytes = await pdfDoc.save();
  fs.writeFileSync(path.resolve(__dirname, 'TimeCopilot-Deck.pdf'), pdfBytes);
  console.log('PDF saved!');

  await browser.close();
})();
