const pptxgen = require('pptxgenjs');
const pptx = new pptxgen();
pptx.layout = 'LAYOUT_WIDE';
const slide = pptx.addSlide();
slide.addText('Test', { x: 1, y: 1, w: 3, h: 1, fontSize: 24 });
slide.addMedia({ type: 'audio', path: '../narration_da/slide_01.mp3', x: 0.2, y: 0.2, w: 0.4, h: 0.4 });
pptx.writeFile({ fileName: 'sample.pptx' });
