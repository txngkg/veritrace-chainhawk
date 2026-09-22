/**
 * 首页动态背景：区块链节点 + 数据流粒子网络动画
 * 仅首页使用，内页不使用动态背景
 */
(function () {
  const canvas = document.getElementById("homeCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  let W = 0, H = 0;
  let particles = [];

  function resize() {
    W = canvas.width = canvas.offsetWidth;
    H = canvas.height = canvas.offsetHeight;
    const count = Math.min(72, Math.max(30, Math.floor((W * H) / 16000)));
    particles = Array.from({ length: count }, () => ({
      x: Math.random() * W,
      y: Math.random() * H,
      vx: (Math.random() - 0.5) * 0.32,
      vy: (Math.random() - 0.5) * 0.32,
      r: Math.random() * 1.8 + 0.9,
      a: Math.random() * Math.PI * 2,
      spin: (Math.random() - 0.5) * 0.02,
    }));
  }

  function step() {
    ctx.clearRect(0, 0, W, H);

    // 节点连线（数据流）
    for (let i = 0; i < particles.length; i++) {
      const a = particles[i];
      for (let j = i + 1; j < particles.length; j++) {
        const b = particles[j];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d < 140) {
          const alpha = 0.24 * (1 - d / 140);
          ctx.strokeStyle = "rgba(59, 130, 246, " + alpha.toFixed(3) + ")";
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
    }

    // 节点 + 旋转光环（区块链节点感）
    for (const p of particles) {
      p.x += p.vx; p.y += p.vy; p.a += p.spin;
      if (p.x < 0 || p.x > W) p.vx *= -1;
      if (p.y < 0 || p.y > H) p.vy *= -1;

      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(96, 165, 250, 0.8)";
      ctx.fill();

      if (p.r > 1.8) {
        ctx.strokeStyle = "rgba(34, 211, 238, 0.22)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r + 5, p.a, p.a + 1.4);
        ctx.stroke();
      }
    }
    requestAnimationFrame(step);
  }

  window.addEventListener("resize", resize);
  resize();
  step();
})();
