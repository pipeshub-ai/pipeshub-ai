const PDFDocument = require('pdfkit');
const fs = require('fs');
const path = require('path');

const OUT = process.argv[2];
const W = 595.28, H = 841.89;
const M = 48, CW = W - 2*M;
const C = {
  navy: '#0B1725', navy2: '#12263A', ink: '#162536', muted: '#5A6B7B',
  teal: '#19A7A1', aqua: '#8DE1D3', orange: '#F4A340', cream: '#F7F5EF',
  white: '#FFFFFF', pale: '#EAF0F2', line: '#D7E0E5', red: '#D64B4B',
  blue: '#274C77', lime: '#B7DB7A', lightBlue: '#DCE8F2'
};
const doc = new PDFDocument({ size: 'A4', margins: {top:0, bottom:0, left:0, right:0}, autoFirstPage: true, compress: true, info: {
  Title: 'IPL 2026 Season Report', Author: 'OpenAI', Subject: 'Retrospective report on the 2026 Indian Premier League'
}});
doc.pipe(fs.createWriteStream(OUT));
doc.font('Helvetica');

function text(str, x, y, w, size=10, color=C.ink, opts={}) {
  doc.font(opts.font || 'Helvetica').fontSize(size).fillColor(color);
  const base = {width:w, lineGap: opts.lineGap ?? 2, align: opts.align || 'left',
    continued: false, paragraphGap: opts.paragraphGap || 0};
  if (opts.link) base.link = opts.link;
  if (opts.underline) base.underline = true;
  if (opts.characterSpacing !== undefined) base.characterSpacing = opts.characterSpacing;
  const h = doc.heightOfString(str, {width:w, font:opts.font || 'Helvetica', fontSize:size, lineGap:opts.lineGap ?? 2, align:opts.align || 'left'});
  doc.text(str, x, y, base);
  return h;
}
function rr(x,y,w,h,r,fill,stroke=null,lineWidth=1) {
  doc.roundedRect(x,y,w,h,r).fillAndStroke(fill, stroke || fill);
  if (stroke && stroke !== fill) doc.lineWidth(lineWidth);
}
function rule(x1,y1,x2,y2,color=C.line,width=1) {
  doc.save().lineWidth(width).strokeColor(color).moveTo(x1,y1).lineTo(x2,y2).stroke().restore();
}
function pageTitle(kicker, title, subtitle='') {
  text(kicker.toUpperCase(), M, 38, CW, 8, C.teal, {font:'Helvetica-Bold', characterSpacing:1.2});
  text(title, M, 56, CW, 25, C.navy, {font:'Helvetica-Bold'});
  let y = 91;
  if (subtitle) y += text(subtitle, M, y, CW, 10, C.muted, {lineGap:3}) + 11;
  rule(M, y, W-M, y, C.line, 0.8);
  return y + 18;
}
function footer(page, dark=false) {
  const col = dark ? '#355066' : C.line;
  rule(M, H-42, W-M, H-42, col, 0.8);
  text('IPL 2026  |  SEASON REPORT', M, H-30, 250, 7.5, dark ? '#A9BAC7' : C.muted, {font:'Helvetica-Bold', characterSpacing:0.6});
  text(String(page).padStart(2,'0'), W-M-28, H-31, 28, 8, dark ? C.aqua : C.teal, {font:'Helvetica-Bold', align:'right'});
}
function tag(label, x, y, w, fill=C.pale, color=C.navy) {
  rr(x,y,w,20,10,fill);
  text(label, x+8,y+5,w-16,7.5,color,{font:'Helvetica-Bold'});
}
function metric(x,y,w,value,label, dark=false) {
  rr(x,y,w,67,10,dark ? C.navy2 : C.white, dark ? '#2B4358' : C.line, 0.6);
  text(value,x+12,y+12,w-24,20,dark?C.aqua:C.navy,{font:'Helvetica-Bold'});
  text(label.toUpperCase(),x+12,y+42,w-24,7.2,dark?'#B8C8D4':C.muted,{font:'Helvetica-Bold',characterSpacing:0.55});
}
function linkRow(title, url, detail, y) {
  const x = M+7, w = CW-14;
  text(title, x, y, w-50, 9.2, C.blue, {font:'Helvetica-Bold', link:url, underline:true});
  text('OPEN LINK', W-M-76, y+1, 68, 7.2, C.teal, {font:'Helvetica-Bold', align:'right', link:url, underline:true});
  const h = text(detail, x, y+14, w, 7.3, C.muted, {lineGap:1.5});
  return 19+h+8;
}

// PAGE 1 - cover
// full bleed editorial cover
 doc.rect(0,0,W,H).fill(C.navy);
 doc.rect(0,0,12,H).fill(C.teal);
 // abstract boundary / stadium light geometry
 doc.save().fillColor(C.navy2).circle(500,148,166).fill().restore();
 doc.save().lineWidth(1.2).strokeColor('#29465B').circle(500,148,126).stroke().restore();
 doc.save().lineWidth(1).strokeColor('#29465B').circle(500,148,91).stroke().restore();
 doc.save().fillColor(C.orange).circle(492,136,8).fill().restore();
 doc.save().fillColor(C.teal).circle(445,201,5).fill().restore();
 text('INDIAN PREMIER LEAGUE  |  2026', M, 64, CW, 9, C.aqua, {font:'Helvetica-Bold', characterSpacing:1.4});
 text('THE TITLE\nDEFENCE', M, 126, 390, 46, C.white, {font:'Helvetica-Bold', lineGap:-3});
 text('IPL 2026 season report', M, 237, 430, 20, C.aqua, {font:'Helvetica-Bold'});
 text('A retrospective of a season decided by a ruthless RCB playoff surge, a record-setting young batter, and a final settled by composure under pressure.', M, 280, 420, 13, '#D7E2E9', {lineGap:5});
 tag('SEASON CLOSE  |  24-31 MAY 2026', M, 367, 214, '#1B354A', C.aqua);
 rule(M, 418, W-M, 418, '#365166', 0.9);
 text('CHAMPIONS', M, 444, 130, 8, '#93A9B9', {font:'Helvetica-Bold', characterSpacing:1});
 text('ROYAL CHALLENGERS BENGALURU', M, 466, 450, 20, C.white, {font:'Helvetica-Bold'});
 text('Second IPL crown  |  successfully retained', M, 496, 360, 10, '#B9C8D3');
 const kx = M, ky = 556, gap=12, kw=(CW-gap*2)/3;
 metric(kx,ky,kw,'5 wickets','Final margin',true);
 metric(kx+kw+gap,ky,kw,'155/8 to 161/5','Final score',true);
 metric(kx+2*(kw+gap),ky,kw,'75* (42)','Kohli in final',true);
 text('Runner-up: Gujarat Titans  |  Final venue: Narendra Modi Stadium, Ahmedabad', M, 653, CW, 10, '#C9D5DE');
 text('Prepared November 2026', M, 694, CW, 8.5, '#91A5B5');
 text('Official IPL match reports and Rajasthan Royals post-season figures underpin this review. Full clickable source notes and scope disclosures appear on page 5.', M, 741, CW, 8, '#AFC0CC', {lineGap:3});
 footer(1,true);

// PAGE 2 - campaign route
 doc.addPage({size:'A4',margins:{top:0,bottom:0,left:0,right:0}});
 let y = pageTitle('Tournament arc','The road to the final','The official match record confirms Match 70 on 24 May, followed by four knockout fixtures from 26 to 31 May.');
 // timeline spine
 const spineX = M+15;
 rule(spineX,y+15,spineX,y+495,'#B7CDD4',2.2);
 const games = [
  {date:'24 MAY', title:'League phase closes', text:'Delhi Capitals beat Kolkata Knight Riders by 40 runs in Match 70 at Eden Gardens. The four-team playoff field is represented in the knockout results below.', venue:'Kolkata', result:'DC 203/5  |  KKR 163 all out', src:'Official IPL Match 70 report'},
  {date:'26 MAY', title:'Qualifier 1 | RCB set the ceiling', text:'RCB made 254/5, then bowled GT out for 162 to win by 92 runs and book a direct place in the final. Rajat Patidar struck 93 not out from 33 balls.', venue:'Dharamshala', result:'RCB 254/5  |  GT 162 all out', src:'Official IPL Qualifier 1 report'},
  {date:'27 MAY', title:'Eliminator | RR survive, then soar', text:'Rajasthan posted 243 and beat Sunrisers Hyderabad by 47 runs. Vaibhav Sooryavanshi lit up the chase setup with 97 from 29 balls.', venue:'Mullanpur', result:'RR 243  |  won by 47 runs', src:'Official IPL Eliminator report'},
  {date:'29 MAY', title:'Qualifier 2 | GT return to the final', text:'After RR made 214/6, GT chased 215 in 18.4 overs to win by seven wickets. Shubman Gill made 104 from 53 balls.', venue:'Mullanpur', result:'GT 219/3  |  RR 214/6', src:'Official IPL Qualifier 2 report'},
  {date:'31 MAY', title:'Final | the holders finish the job', text:'RCB restricted GT to 155/8 and reached 161/5 with two overs left, winning by five wickets and retaining the title.', venue:'Ahmedabad', result:'RCB 161/5  |  GT 155/8', src:'Official IPL Final report'}
 ];
 const cardX=M+36, cardW=CW-36;
 const heights=[69,91,84,84,76];
 games.forEach((g,i)=>{
   const cy=y+i*99;
   doc.save().fillColor(i===4?C.orange:C.teal).circle(spineX,cy+15,5).fill().restore();
   text(g.date,cardX,cy,68,8,C.teal,{font:'Helvetica-Bold',characterSpacing:0.7});
   text(g.title,cardX+72,cy-3,cardW-72,11,C.navy,{font:'Helvetica-Bold'});
   text(g.text,cardX+72,cy+16,cardW-78,8.5,C.ink,{lineGap:2});
   text(g.result,cardX+72,cy+51,cardW-80,7.5,C.blue,{font:'Helvetica-Bold'});
   text(g.venue+'  |  '+g.src,cardX+72,cy+64,cardW-80,7.1,C.muted,{font:'Helvetica-Oblique'});
   if (i<games.length-1) rule(cardX+72,cy+84,W-M,cy+84,C.line,0.6);
 });
 // subtle note card at bottom
 rr(M,y+510,CW,72,9,C.cream,C.line,0.6);
 text('PLAYOFF PATTERN',M+14,y+523,110,7.5,C.teal,{font:'Helvetica-Bold',characterSpacing:0.8});
 text('RCB beat GT in Qualifier 1; RR beat SRH in the Eliminator; GT beat RR in Qualifier 2; RCB then beat GT for the championship.',M+14,y+541,CW-28,9,C.ink,{lineGap:3});
 footer(2);

// PAGE 3 - final analysis
 doc.addPage({size:'A4',margins:{top:0,bottom:0,left:0,right:0}});
 y=pageTitle('Championship match','Final: pressure, pace, poise','31 May 2026  |  Narendra Modi Stadium, Ahmedabad  |  RCB won by five wickets');
 // result banner
 rr(M,y,CW,95,12,C.navy);
 text('GUJARAT TITANS',M+18,y+16,210,8.2,'#B7C7D3',{font:'Helvetica-Bold',characterSpacing:0.7});
 text('155/8',M+18,y+37,170,26,C.white,{font:'Helvetica-Bold'});
 text('20 overs',M+18,y+70,140,8,'#B7C7D3');
 text('v',W/2-5,y+39,20,18,C.orange,{font:'Helvetica-Bold',align:'center'});
 text('ROYAL CHALLENGERS BENGALURU',W/2+26,y+16,220,8.2,C.aqua,{font:'Helvetica-Bold',characterSpacing:0.4});
 text('161/5',W/2+26,y+37,190,26,C.white,{font:'Helvetica-Bold'});
 text('18 overs  |  won by 5 wickets',W/2+26,y+70,220,8,'#B7C7D3');
 let yy=y+117;
 text('THE FIRST INNINGS: GT NEVER FOUND A PLATFORM',M,yy,CW,8,C.teal,{font:'Helvetica-Bold',characterSpacing:0.75});
 yy+=20;
 yy+=text('RCB captain Rajat Patidar chose to bowl. The seamers leaned on short-of-a-length bowling: six of GT\'s eight dismissals came from short deliveries. Washington Sundar held the innings together with an unbeaten 50 from 37 balls, but GT closed on 155/8.',M,yy,CW,10,C.ink,{lineGap:4})+15;
 // insight callout
 rr(M,yy,CW,73,9,'#EAF2F3');
 text('TACTICAL EDGE',M+14,yy+12,110,7.5,C.teal,{font:'Helvetica-Bold',characterSpacing:0.8});
 text('RCB converted a bowling plan into scoreboard pressure; Gujarat lost wickets often enough to deny its middle order a late launch.',M+14,yy+30,CW-28,9,C.navy,{lineGap:3});
 yy+=92;
 text('THE CHASE: A FAST START, THEN KOHLI CLOSES',M,yy,CW,8,C.teal,{font:'Helvetica-Bold',characterSpacing:0.75});
 yy+=20;
 yy+=text('Virat Kohli and Venkatesh Iyer drove RCB to 50 in 3.3 overs - the fastest team fifty in an IPL final. Iyer, introduced as an Impact Player, made 32 from 16. After Rashid Khan briefly reopened the contest with two wickets in four deliveries, Kohli steadied the chase and finished unbeaten on 75 from 42, including a 25-ball fifty.',M,yy,CW,10,C.ink,{lineGap:4})+17;
 // three concise innings moments
 const bw=(CW-18)/3;
 metric(M,yy,bw,'50 in 3.3','Fastest IPL final fifty',false);
 metric(M+bw+9,yy,bw,'75* (42)','Kohli, unbeaten',false);
 metric(M+2*(bw+9),yy,bw,'12 balls','Remaining at finish',false);
 yy+=86;
 text('WHY THE FINAL MATTERED',M,yy,CW,8,C.teal,{font:'Helvetica-Bold',characterSpacing:0.75});
 yy+=18;
 text('RCB became only the third franchise to defend the IPL title, joining Chennai Super Kings and Mumbai Indians. The title was RCB\'s second. Gujarat returned to a final but fell short again, three years after its 2023 defeat to CSK.',M,yy,CW,9.5,C.ink,{lineGap:3.5});
 text('Source: Official IPL Final match report; clickable source in notes on page 5.',M,yy+54,CW,7.5,C.muted,{font:'Helvetica-Oblique'});
 footer(3);

// PAGE 4 - performers
 doc.addPage({size:'A4',margins:{top:0,bottom:0,left:0,right:0}});
 y=pageTitle('People & performance','The season belonged to Sooryavanshi','The numbers below are tournament figures stated in the official Rajasthan Royals season recap and IPL season material.');
 // Big leader panel
 rr(M,y,CW,116,12,C.navy);
 text('ORANGE CAP  |  MVP  |  EMERGING PLAYER',M+18,y+16,CW-36,8,C.aqua,{font:'Helvetica-Bold',characterSpacing:0.7});
 text('Vaibhav Sooryavanshi',M+18,y+39,CW-36,22,C.white,{font:'Helvetica-Bold'});
 text('776 runs  |  16 matches  |  strike rate 237.30',M+18,y+74,CW-36,10,'#D0DDE5');
 text('Also won Super Sixes and Super Striker of the Season.',M+18,y+94,CW-36,8.5,'#AFC2CE');
 yy=y+137;
 text('TOP RUN-SCORERS',M,yy,250,8,C.teal,{font:'Helvetica-Bold',characterSpacing:0.8});
 // table
 const tx=M, ty=yy+18, cols=[250,90,90,69];
 const headers=['BATTER','RUNS','MATCHES','STRIKE RATE'];
 let x=tx;
 doc.rect(tx,ty,CW,24).fill(C.navy);
 headers.forEach((h,i)=>{text(h,x+8,ty+7,cols[i]-16,7.4,C.white,{font:'Helvetica-Bold',characterSpacing:0.3,align:i===0?'left':'right'}); x+=cols[i];});
 const batters=[['Vaibhav Sooryavanshi (RR)','776','16','237.30'],['Shubman Gill (GT)','732','16','163.02'],['Sai Sudharsan (GT)','722','17','157.98'],['Virat Kohli (RCB)','675','16','165.84']];
 let by=ty+24;
 batters.forEach((row,r)=>{
   doc.rect(tx,by,CW,29).fill(r%2===0?'#F1F5F6':'#FFFFFF');
   let xx=tx;
   row.forEach((v,i)=>{text(v,xx+8,by+9,cols[i]-16,8.3,i===0?C.ink:C.navy,{font:i===0?'Helvetica-Bold':'Helvetica',align:i===0?'left':'right'});xx+=cols[i];});
   by+=29;
 });
 yy=by+22;
 // split leader cards
 const half=(CW-14)/2;
 rr(M,yy,half,107,10,'#E8F3F2');
 text('PURPLE CAP',M+14,yy+13,half-28,7.6,C.teal,{font:'Helvetica-Bold',characterSpacing:0.7});
 text('Kagiso Rabada (GT)',M+14,yy+31,half-28,12,C.navy,{font:'Helvetica-Bold'});
 text('29 wickets',M+14,yy+52,half-28,17,C.navy,{font:'Helvetica-Bold'});
 text('Bhuvneshwar Kumar: 28  |  Jofra Archer: 25',M+14,yy+80,half-28,7.2,C.muted);
 rr(M+half+14,yy,half,107,10,'#F8F1E4');
 text('PLAYER OF THE FINAL',M+half+28,yy+13,half-28,7.6,'#A96613',{font:'Helvetica-Bold',characterSpacing:0.7});
 text('Virat Kohli (RCB)',M+half+28,yy+31,half-28,12,C.navy,{font:'Helvetica-Bold'});
 text('75* off 42',M+half+28,yy+52,half-28,17,C.navy,{font:'Helvetica-Bold'});
 text('A 25-ball half-century in a title chase.',M+half+28,yy+80,half-28,7.5,C.muted);
 yy+=126;
 text('RECORDS & DEFINING KNOCKS',M,yy,CW,8,C.teal,{font:'Helvetica-Bold',characterSpacing:0.75});
 yy+=18;
 const bullets=[
  'Sooryavanshi hit 72 sixes - an IPL-season record - and made a 36-ball century against Sunrisers Hyderabad.',
  'His 97 from 29 balls in the Eliminator included eight powerplay sixes, the most by a batter in that phase of an IPL innings.',
  'Rajat Patidar\'s unbeaten 93 from 33 powered RCB to the record-high playoff total of 254/5 in Qualifier 1.'
 ];
 bullets.forEach((b,i)=>{
   doc.save().fillColor(i===1?C.orange:C.teal).circle(M+4,yy+7,2.4).fill().restore();
   const h=text(b,M+14,yy,CW-14,8.8,C.ink,{lineGap:2.4});
   yy+=h+9;
 });
 text('Source: Rajasthan Royals official awards recap and IPL official season/match reports; see linked sources on page 5.',M,yy+1,CW,7.4,C.muted,{font:'Helvetica-Oblique'});
 footer(4);

// PAGE 5 - scope & sources
 doc.addPage({size:'A4',margins:{top:0,bottom:0,left:0,right:0}});
 y=pageTitle('Sources & scope','A documented retrospective','Source notes are linked directly to the official reports and post-season recap used in this document.');
 rr(M,y,CW,107,10,C.cream,C.line,0.6);
 text('SCOPE NOTE',M+15,y+14,120,7.6,C.teal,{font:'Helvetica-Bold',characterSpacing:0.8});
 text('The primary-source record reviewed here establishes the closing calendar: Match 70 on 24 May and playoff matches on 26, 27, 29 and 31 May. The exact season opening date and a complete venue list are not stated because the linked official schedule PDF could not be text-verified. The report therefore avoids asserting an opening date or unconfirmed tournament-wide counts.',M+15,y+32,CW-30,8.7,C.ink,{lineGap:3});
 yy=y+128;
 text('CLICKABLE SOURCE NOTES',M,yy,CW,8,C.teal,{font:'Helvetica-Bold',characterSpacing:0.8});
 yy+=21;
 const sources=[
  ['Official IPL - Final: RCB v GT match report (31 May)','https://www.iplt20.com/news/article/tata-ipl-2026-final-rcb-v-gt-match-report','Final result, scores, Kohli innings, title defence, tactical account.'],
  ['Official IPL - Qualifier 1: RCB v GT report (26 May)','https://www.iplt20.com/','RCB 254/5, GT 162; 92-run win; Patidar 93*; Dharamshala.'],
  ['Official IPL - Eliminator: SRH v RR report (27 May)','https://www.iplt20.com/','RR win by 47; Sooryavanshi 97 off 29; Mullanpur.'],
  ['Official IPL - Qualifier 2: GT v RR report (29 May)','https://www.iplt20.com/','GT chase of 215; Gill 104; Mullanpur venue.'],
  ['Official IPL - Match 70: KKR v DC report (24 May)','https://www.iplt20.com/','Final league match number and date.'],
  ['Rajasthan Royals - Sooryavanshi awards and batting stats','https://www.rajasthanroyals.com/','Orange Cap, MVP, awards, batting figures and records.'],
  ['Official IPL - Season archive and performer features','https://www.iplt20.com/','Season video index includes Rabada 29, Bhuvneshwar 28 and Archer 25 wickets.'],
  ['Official IPL - 2026 full schedule PDF','https://www.iplt20.com/','Linked as schedule reference; not used for unverified opening-date claims.']
 ];
 sources.forEach(([title,url,detail])=>{
   yy+=linkRow(title,url,detail,yy);
   rule(M,yy-6,W-M,yy-6,C.line,0.6);
 });
 text('All season-specific figures are attributed to the linked issuer/team pages; independently unavailable details are identified in the scope note above.',M,yy+8,CW,7.6,C.muted,{font:'Helvetica-Oblique',lineGap:2});
 footer(5);
 doc.end();
