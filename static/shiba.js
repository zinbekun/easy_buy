// 進捗バーの上を転がる丸い柴犬。正面向きのまま体の前後軸で回し、角度は階段状に変える。
const Shiba = (() => {
  const SPRITE = [
    "............................................",
    "......K............................K........",
    "......KK..........................KK........",
    "......KOK........................KOK........",
    "......KOOKK....................KKOOK........",
    "......KOOOOK..................KOOOOK........",
    "......KPPOOOK.KKKKKKKKKKKKKK.KOOOPPK........",
    "......KPPPOOOKOOODDDDDDDDOOOKOOOPPPK........",
    ".....KOPPPOOOODDDDDDDDDDDDDDOOOOPPPOK.......",
    ".....KOPOOOOOOOOOOOOOOOOOOOOOOOOOOPOK.......",
    ".....KOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOK.......",
    ".....KOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOK.......",
    ".....KOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOKKKKKK.",
    "....KOOOOOOOWWWOOOOOOOOOOOOWWWOOOOOOOOOOOOK.",
    "....KOOOOOOWWWWWOOOOOOOOOOWWWWWOOOOOOOWWWWOK",
    "...KOOOOOOOWWWWWOOOOOOOOOOWWWWWOOOOOOOODDWOK",
    "...KOOOOOOOOWWWOOOOOOOOOOOOWWWOOOOOOOOODDWOK",
    "..KOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOWWOK",
    "..KOOOOOOWWWWKOOOOOOOOOOOOOOKWWWWOOOOOOOOOK.",
    "..KOOOOWWWWWWKKWOOOOOOOOOOWWKKWWWWWOOOOOKKK.",
    "..KOOOWWWWWWKKKWWOOOOOOOOWWKKKWWWWWWOOOK....",
    "..KOOWWWWWWWKKKWWWWWWWWWWWWKKKWWWWWWWOOK....",
    "..KOOWWWWWWWWKWWWWWWWWWWWWWWKWWWWWWWWOOK....",
    "..KOOWWWWWWWWWWWWWKKKKKKWWWWWWWWWWWWWOOK....",
    "..KOOWPPWWWWWWWWWWWKKKKWWWWWWWWWWWPPWOOK....",
    "...KOPPPPWWWWWWWWWWWKKWWWWWWWWWWWPPPPOK.....",
    "...KOPPPPWWWWWWWWKWWKKWWKWWWWWWWWPPPPOK.....",
    "....KOPPWWWWWWWWWWKKWWKKWWWWWWWWWWPPOK......",
    "....KOWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWOK......",
    ".....KOWWWWWWWWWWWWWWWWWWWWWWWWWWWWOK.......",
    "......KWWWWWWWWWWWWWWWWWWWWWWWWWWWWK........",
    ".......KWWWWWWWWWWWWWWWWWWWWWWWWWWK.........",
    "........KWWWWWWWWWWWWWWWWWWWWWWWWK..........",
    ".........KWWWWWWWWWWWWWWWWWWWWWWK...........",
    "........KWWWWWWWWWWWWWWWWWWWWWWWWK..........",
    "........KWWWWWOOWKKKKKKKKWOOWWWWWK..........",
    ".........KWWWWWWK........KWWWWWWK...........",
    "..........KKKKKK..........KKKKKK............",
  ];
  const COLORS = { K: "#3a2416", O: "#d9853b", D: "#bd6c2c", W: "#fbeedb", P: "#eaa0a0" };
  const STEPS = 16;
  const SCALE = 1.5;

  const sprite = document.createElement("canvas");
  sprite.width = Math.max(...SPRITE.map((r) => r.length));
  sprite.height = SPRITE.length;
  const sctx = sprite.getContext("2d");
  SPRITE.forEach((row, y) => [...row].forEach((c, x) => {
    if (COLORS[c]) { sctx.fillStyle = COLORS[c]; sctx.fillRect(x, y, 1, 1); }
  }));

  function draw(canvas, angle) {
    const ctx = canvas.getContext("2d");
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.save();
    ctx.translate(canvas.width / 2, canvas.height / 2);
    ctx.rotate(angle);
    ctx.scale(SCALE, SCALE);
    ctx.drawImage(sprite, -sprite.width / 2, -sprite.height / 2);
    ctx.restore();
  }

  return class {
    constructor(canvas) {
      this.canvas = canvas;
      this.frame = null;
      this.progress = 0;
      this.shown = 0;
    }
    start() {
      this.stop();
      this.canvas.classList.remove("arrived");
      this.shown = 0;
      this.progress = 0;
      this.tick();
    }
    setProgress(progress) {
      this.progress = progress;
    }
    tick() {
      this.shown += (this.progress - this.shown) * 0.08;
      this.place(this.shown);
      const step = Math.round(this.shown * 180) + Math.floor(performance.now() / 110);
      draw(this.canvas, (step % STEPS) / STEPS * Math.PI * 2);
      this.frame = requestAnimationFrame(() => this.tick());
    }
    place(ratio) {
      this.canvas.style.left = `${ratio * 100}%`;
    }
    stop() {
      if (this.frame !== null) cancelAnimationFrame(this.frame);
      this.frame = null;
    }
    arrive() {
      this.stop();
      this.place(1);
      draw(this.canvas, 0);
      this.canvas.classList.add("arrived");
    }
  };
})();
