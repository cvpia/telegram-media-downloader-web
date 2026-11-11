(() => {
  "use strict";

  const { useEffect, useRef, useState } = React;
  const { AnimatePresence, motion } = window.Motion || {};
  const appVersion = document.querySelector('meta[name="app-version"]')?.content || "3.12.1";
  const asset = (path) => path + "?v=" + encodeURIComponent(appVersion);

  const screenshots = [
    {
      src: "/static/landing/monitor.png",
      title: "下载监控中心",
      meta: "实时速度 · 队列状态 · 磁盘资源",
      alt: "Telegram Media Downloader 下载监控真实界面",
    },
    {
      src: "/static/landing/filter.png",
      title: "频道筛选规则",
      meta: "点选条件 · 自动生成表达式",
      alt: "频道下载过滤规则编辑器界面",
    },
    {
      src: "/static/landing/setup.png",
      title: "浏览器配置向导",
      meta: "五步完成 · 无需编辑 YAML",
      alt: "Telegram 登录与下载设置向导界面",
    },
  ];

  const features = [
    {
      index: "01",
      type: "chip",
      label: "LOCAL SECURITY",
      title: "本地优先的安全边界",
      body: "凭据在服务端加密保存，Telegram 会话与媒体文件留在你的设备。没有第三方云盘中转，也没有不可见的数据留存。",
      tags: ["Fernet 加密", "SQLite 持久化", "本地媒体"],
    },
    {
      index: "02",
      type: "flow",
      label: "RESUMABLE STREAM",
      title: "为大体量内容而生",
      body: "分块下载、断点续传与指数退避协同工作。网络中断不会摧毁进度，失败资源会在队列结束后优先重试。",
      tags: ["10 路并发", "断点续传", "自动重试"],
    },
    {
      index: "03",
      type: "lattice",
      label: "AUTOMATION MESH",
      title: "一次配置，持续监听",
      body: "按频道、关键词、大小与媒体类型组合规则。历史消息自动补转，新内容持续监听，复杂搬运变成安静运行的基础设施。",
      tags: ["历史补转", "实时监听", "组合筛选"],
    },
  ];

  const showcaseStats = [
    { index: "01", title: "增量同步", body: "频道与会话缓存优先呈现，后台仅在数据变化时刷新，不让你盯着加载动画。" },
    { index: "02", title: "轻量预览", body: "图片、视频与缩略图按需加载，大频道也能保持顺畅的浏览和滚动体验。" },
    { index: "03", title: "实时反馈", body: "SSE 推送速度、进度、剩余时间与队列状态，所有关键信息都在一个视图内。" },
  ];

  const faqs = [
    {
      question: "数据会上传到第三方服务器吗？",
      answer: "不会。应用运行在你自己的设备或服务器中，媒体写入本机 downloads 目录，配置与下载历史保存在本地 SQLite。只有连接 Telegram 所必需的网络请求会发往 Telegram。",
    },
    {
      question: "是否需要每次手动编辑 YAML？",
      answer: "不需要。首次使用可在浏览器内完成网络、管理员、Telegram 凭据、登录与下载偏好设置；旧版 YAML 仍然支持导入和导出。",
    },
    {
      question: "下载中断后会重新开始吗？",
      answer: "不会。临时文件与已下载分块会被保留，任务恢复时继续传输剩余部分。单文件失败还会自动刷新文件引用并指数退避重试。",
    },
    {
      question: "可以在手机或小屏设备上使用吗？",
      answer: "可以。官网和后台都提供响应式布局。3D 首屏会在移动端自动降低粒子数量、几何细分与像素比，并在离开视口时停止渲染。",
    },
    {
      question: "部署复杂吗？需要什么环境？",
      answer: "推荐使用 Docker Compose，一条命令即可启动；也支持 Python 3.12 / 3.13 原生运行。开放公网访问时建议在反向代理层启用 HTTPS。",
    },
  ];

  const navLinks = [
    { href: "#product", label: "产品能力" },
    { href: "#network", label: "数据网络" },
    { href: "#interface", label: "真实界面" },
    { href: "#faq", label: "常见问题" },
  ];

  const iconPaths = {
    shield: <><path d="M12 3 19 6v5c0 4.7-2.8 8-7 10-4.2-2-7-5.3-7-10V6l7-3Z" /><path d="m9 12 2 2 4-4" /></>,
    bolt: <><path d="m13 2-8 12h7l-1 8 8-12h-7l1-8Z" /></>,
    radar: <><circle cx="12" cy="12" r="3" /><circle cx="12" cy="12" r="8" /><path d="M12 4v2m8 6h-2M4 12h2m10.7-5.7-1.4 1.4M6.7 17.3l1.4-1.4" /></>,
    lock: <><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" /></>,
    layers: <><path d="m12 3 9 5-9 5-9-5 9-5Z" /><path d="m3 12 9 5 9-5M3 16l9 5 9-5" /></>,
    spark: <><path d="m12 3 1.5 5.5L19 10l-5.5 1.5L12 17l-1.5-5.5L5 10l5.5-1.5L12 3Z" /><path d="m5 16 .8 2.2L8 19l-2.2.8L5 22l-.8-2.2L2 19l2.2-.8L5 16Z" /></>,
    arrow: <><path d="M5 12h14m-5-5 5 5-5 5" /></>,
    external: <><path d="M14 4h6v6M20 4 10 14" /><path d="M20 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h4" /></>,
  };

  function Icon({ name, className = "" }) {
    return (
      <svg className={className} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        {iconPaths[name] || iconPaths.spark}
      </svg>
    );
  }

  function FeatureModel({ type }) {
    if (type === "chip") {
      return (
        <div className="model-chip" aria-hidden="true">
          <div className="chip-glow" />
          <div className="chip-board">
            {[1, 2, 3, 4, 5, 6].map((item) => <span className="chip-pin" key={item} />)}
            <span className="chip-core" />
          </div>
        </div>
      );
    }

    if (type === "flow") {
      return (
        <div className="model-flow" aria-hidden="true">
          <span className="flow-plane" />
          <span className="flow-plane" />
          <svg className="flow-path" viewBox="0 0 280 220">
            <defs>
              <linearGradient id="flowGradient" x1="0" x2="1" y1="0" y2="1">
                <stop offset="0" stopColor="#4ee9ff" stopOpacity=".15" />
                <stop offset=".5" stopColor="#63f0ff" stopOpacity=".95" />
                <stop offset="1" stopColor="#6a8dff" stopOpacity=".2" />
              </linearGradient>
            </defs>
            <path d="M37 153 C86 57 153 207 241 74" />
            <path d="M51 71 C120 172 168 32 239 147" />
          </svg>
          <span className="flow-orb" />
          <span className="flow-particle p1" />
          <span className="flow-particle p2" />
          <span className="flow-particle p3" />
        </div>
      );
    }

    return (
      <div className="model-lattice" aria-hidden="true">
        <svg className="lattice-svg" viewBox="0 0 280 220">
          <line x1="40" y1="54" x2="134" y2="106" />
          <line x1="134" y1="106" x2="235" y2="55" />
          <line x1="40" y1="54" x2="78" y2="171" />
          <line x1="78" y1="171" x2="209" y2="164" />
          <line x1="209" y1="164" x2="235" y2="55" />
          <line x1="134" y1="106" x2="149" y2="26" />
          <line x1="134" y1="106" x2="78" y2="171" />
          <line x1="134" y1="106" x2="209" y2="164" />
        </svg>
        <span className="lattice-node" style={{ top: "24%", left: "14%" }} />
        <span className="lattice-node" style={{ top: "50%", left: "48%" }} />
        <span className="lattice-node" style={{ top: "25%", left: "84%" }} />
        <span className="lattice-node" style={{ top: "78%", left: "28%" }} />
        <span className="lattice-node" style={{ top: "75%", left: "75%" }} />
        <span className="lattice-node" style={{ top: "12%", left: "53%" }} />
        <span className="lattice-core" />
      </div>
    );
  }

  function HeroScene() {
    const hostRef = useRef(null);

    useEffect(() => {
      const host = hostRef.current;
      if (!host || !window.THREE) return undefined;

      const THREE = window.THREE;
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const mobile = window.matchMedia("(max-width: 860px)").matches;
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 80);
      camera.position.set(0, 0.15, 6.4);

      const renderer = new THREE.WebGLRenderer({
        alpha: true,
        antialias: !mobile,
        powerPreference: "high-performance",
      });
      renderer.setClearColor(0x000000, 0);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, mobile ? 1.2 : 1.6));
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      host.appendChild(renderer.domElement);
      renderer.domElement.setAttribute("aria-hidden", "true");
      renderer.domElement.style.width = "100%";
      renderer.domElement.style.height = "100%";

      const group = new THREE.Group();
      group.position.z = -0.42;
      scene.add(group);

      const coreGeometry = new THREE.IcosahedronGeometry(mobile ? 1.24 : 1.43, mobile ? 3 : 5);
      const basePositions = new Float32Array(coreGeometry.attributes.position.array);
      const coreMaterial = new THREE.MeshPhysicalMaterial({
        color: 0x0b365d,
        metalness: 0.56,
        roughness: 0.2,
        clearcoat: 0.95,
        clearcoatRoughness: 0.2,
        emissive: 0x061b39,
        emissiveIntensity: 0.42,
        transparent: true,
        opacity: 0.9,
      });
      const core = new THREE.Mesh(coreGeometry, coreMaterial);
      core.rotation.set(-0.25, 0.28, 0.08);
      group.add(core);

      const innerGlow = new THREE.Mesh(
        new THREE.IcosahedronGeometry(mobile ? 1.25 : 1.44, 2),
        new THREE.MeshBasicMaterial({
          color: 0x52dff5,
          transparent: true,
          opacity: 0.055,
          wireframe: true,
          blending: THREE.AdditiveBlending,
        })
      );
      group.add(innerGlow);

      const wire = new THREE.Mesh(
        new THREE.IcosahedronGeometry(mobile ? 1.9 : 2.05, 2),
        new THREE.MeshBasicMaterial({
          color: 0x56dff8,
          wireframe: true,
          transparent: true,
          opacity: 0.105,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
        })
      );
      wire.rotation.set(0.72, -0.18, -0.36);
      group.add(wire);

      const ringGroup = new THREE.Group();
      const ringMaterial = new THREE.MeshBasicMaterial({
        color: 0x6bcfff,
        transparent: true,
        opacity: 0.19,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      });
      const ringOne = new THREE.Mesh(new THREE.TorusGeometry(2.45, 0.009, 5, 180), ringMaterial);
      ringOne.rotation.set(1.08, 0.16, -0.35);
      const ringTwo = new THREE.Mesh(new THREE.TorusGeometry(2.82, 0.006, 5, 180), ringMaterial.clone());
      ringTwo.material.opacity = 0.1;
      ringTwo.rotation.set(0.52, 1.12, 0.72);
      ringGroup.add(ringOne, ringTwo);
      group.add(ringGroup);

      const particleCount = mobile ? 430 : 980;
      const particlePositions = new Float32Array(particleCount * 3);
      const particleSizes = new Float32Array(particleCount);
      for (let i = 0; i < particleCount; i += 1) {
        const radius = 2.6 + Math.pow(Math.random(), 0.72) * 5.5;
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        particlePositions[i * 3] = radius * Math.sin(phi) * Math.cos(theta);
        particlePositions[i * 3 + 1] = radius * Math.cos(phi) * 0.62;
        particlePositions[i * 3 + 2] = radius * Math.sin(phi) * Math.sin(theta) - 1.3;
        particleSizes[i] = 0.018 + Math.random() * 0.045;
      }
      const particleGeometry = new THREE.BufferGeometry();
      particleGeometry.setAttribute("position", new THREE.BufferAttribute(particlePositions, 3));
      const particles = new THREE.Points(
        particleGeometry,
        new THREE.PointsMaterial({
          color: 0x75dff3,
          size: mobile ? 0.038 : 0.032,
          transparent: true,
          opacity: 0.62,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
          sizeAttenuation: true,
        })
      );
      scene.add(particles);

      const ambient = new THREE.AmbientLight(0x7ba6d4, 1.25);
      const hemisphere = new THREE.HemisphereLight(0x86dfff, 0x040812, 1.3);
      const keyLight = new THREE.DirectionalLight(0x6eeeff, 3.2);
      keyLight.position.set(4.5, 4, 5);
      const rimLight = new THREE.PointLight(0x4b72ff, 24, 14, 2);
      rimLight.position.set(-4, 1.6, 2.5);
      const softFill = new THREE.PointLight(0x49badb, 11, 12, 2);
      softFill.position.set(2.5, -3.2, 3.5);
      scene.add(ambient, hemisphere, keyLight, rimLight, softFill);

      const pointer = { x: 0, y: 0, targetX: 0, targetY: 0 };
      const handlePointer = (event) => {
        pointer.targetX = (event.clientX / Math.max(window.innerWidth, 1) - 0.5) * 2;
        pointer.targetY = (event.clientY / Math.max(window.innerHeight, 1) - 0.5) * 2;
        document.body.classList.add("has-pointer");
      };
      const handlePointerLeave = () => document.body.classList.remove("has-pointer");
      window.addEventListener("pointermove", handlePointer, { passive: true });
      document.documentElement.addEventListener("pointerleave", handlePointerLeave);

      const resize = () => {
        const width = host.clientWidth || window.innerWidth;
        const height = host.clientHeight || window.innerHeight;
        renderer.setSize(width, height, false);
        camera.aspect = width / Math.max(height, 1);
        camera.updateProjectionMatrix();
      };
      const resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(host);
      resize();

      let inView = true;
      const visibilityObserver = new IntersectionObserver((entries) => {
        inView = entries[0]?.isIntersecting ?? true;
      }, { rootMargin: "120px" });
      visibilityObserver.observe(host);

      let frameId = 0;
      let previous = 0;
      let elapsed = 0;
      let particleFlow = 0;
      let targetFps = reducedMotion ? 1 : 45;
      let renderSamples = 0;
      let renderTotal = 0;
      const clock = new THREE.Clock();

      const render = (time) => {
        frameId = requestAnimationFrame(render);
        if (!inView || document.hidden) {
          previous = time;
          return;
        }
        const interval = 1000 / targetFps;
        if (time - previous < interval) return;

        const start = performance.now();
        const delta = Math.min(clock.getDelta(), 0.05);
        previous = time;
        elapsed += delta;

        pointer.x += (pointer.targetX - pointer.x) * 0.055;
        pointer.y += (pointer.targetY - pointer.y) * 0.055;
        const scrollProgress = window.scrollY / Math.max(window.innerHeight, 1);

        group.rotation.y = 0.28 + elapsed * 0.045 + pointer.x * 0.095;
        group.rotation.x = -0.08 + pointer.y * 0.06 + scrollProgress * 0.13;
        group.position.y = -scrollProgress * 1.05;
        group.position.x = pointer.x * 0.2;

        core.rotation.y = elapsed * 0.09;
        core.rotation.z = Math.sin(elapsed * 0.21) * 0.09;
        innerGlow.rotation.y = -elapsed * 0.045;
        wire.rotation.y += delta * 0.045;
        wire.rotation.z -= delta * 0.015;
        ringGroup.rotation.z += delta * 0.026;
        const pointerEnergy = Math.min(Math.sqrt(pointer.x * pointer.x + pointer.y * pointer.y), 1.4);
        particleFlow += delta * (0.008 + pointerEnergy * 0.052);
        particles.rotation.y = particleFlow + pointer.x * 0.025;
        particles.rotation.x = pointer.y * 0.012;
        particles.material.opacity = 0.52 + Math.sin(elapsed * 0.55) * 0.08;

        if (!reducedMotion && (Math.floor(elapsed * 45) % 2 === 0)) {
          const positions = coreGeometry.attributes.position.array;
          for (let i = 0; i < positions.length; i += 3) {
            const x = basePositions[i];
            const y = basePositions[i + 1];
            const z = basePositions[i + 2];
            const radius = Math.sqrt(x * x + y * y + z * z) || 1;
            const wave =
              Math.sin(x * 3.4 + elapsed * 0.95) * 0.038 +
              Math.sin(y * 5.2 - elapsed * 0.7) * 0.025 +
              Math.cos(z * 6.2 + elapsed * 0.47) * 0.018;
            const scale = (radius + wave) / radius;
            positions[i] = x * scale;
            positions[i + 1] = y * scale;
            positions[i + 2] = z * scale;
          }
          coreGeometry.attributes.position.needsUpdate = true;
          coreGeometry.computeVertexNormals();
        }

        renderer.render(scene, camera);

        renderSamples += 1;
        renderTotal += performance.now() - start;
        if (renderSamples >= 90) {
          const average = renderTotal / renderSamples;
          targetFps = average > 17 ? 30 : average < 7 && !mobile ? 45 : targetFps;
          host.dataset.adaptiveFps = String(targetFps);
          renderSamples = 0;
          renderTotal = 0;
        }
      };

      frameId = requestAnimationFrame(render);

      return () => {
        cancelAnimationFrame(frameId);
        resizeObserver.disconnect();
        visibilityObserver.disconnect();
        window.removeEventListener("pointermove", handlePointer);
        document.documentElement.removeEventListener("pointerleave", handlePointerLeave);
        document.body.classList.remove("has-pointer");
        scene.traverse((object) => {
          object.geometry?.dispose?.();
          if (Array.isArray(object.material)) object.material.forEach((material) => material.dispose?.());
          else object.material?.dispose?.();
        });
        renderer.dispose();
        renderer.domElement.remove();
      };
    }, []);

    return <div className="hero-canvas" ref={hostRef} data-scene="hero" aria-hidden="true" />;
  }

  function GlobeScene() {
    const hostRef = useRef(null);

    useEffect(() => {
      const host = hostRef.current;
      if (!host || !window.THREE) return undefined;

      const THREE = window.THREE;
      const mobile = window.matchMedia("(max-width: 860px)").matches;
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 30);
      camera.position.set(0, 0.08, 4.2);

      const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: !mobile });
      renderer.setClearColor(0x000000, 0);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.4));
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.domElement.style.width = "100%";
      renderer.domElement.style.height = "100%";
      renderer.domElement.setAttribute("aria-hidden", "true");
      host.appendChild(renderer.domElement);

      const globe = new THREE.Group();
      globe.rotation.set(0.12, -0.2, -0.16);
      scene.add(globe);

      const sphere = new THREE.Mesh(
        new THREE.SphereGeometry(1.42, mobile ? 36 : 56, mobile ? 28 : 44),
        new THREE.MeshPhysicalMaterial({
          color: 0x061b37,
          metalness: 0.5,
          roughness: 0.34,
          clearcoat: 0.72,
          clearcoatRoughness: 0.4,
          transparent: true,
          opacity: 0.82,
          emissive: 0x04152c,
          emissiveIntensity: 0.7,
        })
      );
      globe.add(sphere);

      const wireSphere = new THREE.Mesh(
        new THREE.SphereGeometry(1.445, 30, 20),
        new THREE.MeshBasicMaterial({
          color: 0x2c9ac1,
          wireframe: true,
          transparent: true,
          opacity: 0.11,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
        })
      );
      globe.add(wireSphere);

      const pointCount = mobile ? 320 : 620;
      const points = [];
      for (let i = 0; i < pointCount; i += 1) {
        const y = 1 - (i / (pointCount - 1)) * 2;
        const radiusAtY = Math.sqrt(1 - y * y);
        const theta = i * Math.PI * (3 - Math.sqrt(5));
        points.push(new THREE.Vector3(Math.cos(theta) * radiusAtY, y, Math.sin(theta) * radiusAtY).multiplyScalar(1.447));
      }
      const pointsGeometry = new THREE.BufferGeometry().setFromPoints(points);
      const pointCloud = new THREE.Points(
        pointsGeometry,
        new THREE.PointsMaterial({
          color: 0x60ddec,
          size: 0.017,
          transparent: true,
          opacity: 0.55,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
        })
      );
      globe.add(pointCloud);

      const arcMaterial = new THREE.LineBasicMaterial({
        color: 0x5be6ff,
        transparent: true,
        opacity: 0.32,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      });
      const vectorFor = (lat, lon, height = 1.45) => {
        const phi = (90 - lat) * Math.PI / 180;
        const theta = (lon + 180) * Math.PI / 180;
        return new THREE.Vector3(
          -height * Math.sin(phi) * Math.cos(theta),
          height * Math.cos(phi),
          height * Math.sin(phi) * Math.sin(theta)
        );
      };
      const connections = [
        [31.2, 121.5, 40.7, -74.0],
        [31.2, 121.5, 51.5, -0.1],
        [40.7, -74.0, 51.5, -0.1],
        [35.7, 139.7, 1.35, 103.8],
        [31.2, 121.5, -33.9, 151.2],
        [51.5, -0.1, 25.2, 55.3],
        [40.7, -74.0, -23.5, -46.6],
      ];
      connections.forEach((item, index) => {
        const start = vectorFor(item[0], item[1], 1.47);
        const end = vectorFor(item[2], item[3], 1.47);
        const middle = start.clone().add(end).multiplyScalar(0.5);
        const arcHeight = 1.72 + Math.min(start.distanceTo(end) * 0.16, 0.34) + (index % 3) * 0.04;
        middle.normalize().multiplyScalar(arcHeight);
        const curve = new THREE.QuadraticBezierCurve3(start, middle, end);
        const geometry = new THREE.BufferGeometry().setFromPoints(curve.getPoints(40));
        const line = new THREE.Line(geometry, arcMaterial.clone());
        line.material.opacity = 0.16 + (index % 3) * 0.07;
        line.userData.phase = index * 0.56;
        globe.add(line);
      });

      const markerMaterial = new THREE.MeshBasicMaterial({
        color: 0xb6fbff,
        transparent: true,
        opacity: 0.95,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      });
      const markerCoordinates = [
        [31.2, 121.5],
        [40.7, -74.0],
        [51.5, -0.1],
        [35.7, 139.7],
        [1.35, 103.8],
        [-33.9, 151.2],
      ];
      const markers = markerCoordinates.map((coords) => {
        const marker = new THREE.Mesh(new THREE.SphereGeometry(0.025, 10, 10), markerMaterial.clone());
        marker.position.copy(vectorFor(coords[0], coords[1], 1.46));
        marker.userData.origin = marker.position.clone();
        globe.add(marker);
        return marker;
      });

      const halo = new THREE.Mesh(
        new THREE.RingGeometry(1.6, 1.605, 120),
        new THREE.MeshBasicMaterial({
          color: 0x50cde8,
          transparent: true,
          opacity: 0.18,
          side: THREE.DoubleSide,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
        })
      );
      halo.rotation.x = Math.PI / 2.55;
      globe.add(halo);

      scene.add(new THREE.AmbientLight(0x7fcbe8, 1.1));
      const key = new THREE.DirectionalLight(0x90eeff, 2.8);
      key.position.set(3, 3, 4);
      const fill = new THREE.PointLight(0x536dff, 20, 9, 2);
      fill.position.set(-3, -1, 2);
      scene.add(key, fill);

      const resize = () => {
        const width = host.clientWidth || 1;
        const height = host.clientHeight || 1;
        renderer.setSize(width, height, false);
        camera.aspect = width / Math.max(height, 1);
        camera.updateProjectionMatrix();
      };
      const resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(host);
      resize();

      let inView = true;
      const visibilityObserver = new IntersectionObserver((entries) => {
        inView = entries[0]?.isIntersecting ?? true;
      }, { rootMargin: "180px" });
      visibilityObserver.observe(host);

      const clock = new THREE.Clock();
      let frameId = 0;
      let elapsed = 0;
      let previous = 0;
      const render = (time) => {
        frameId = requestAnimationFrame(render);
        if (!inView || document.hidden) {
          previous = time;
          return;
        }
        if (time - previous < 1000 / (reducedMotion ? 1 : 30)) return;
        previous = time;
        elapsed += Math.min(clock.getDelta(), 0.08);
        globe.rotation.y = -0.22 + elapsed * 0.075;
        globe.rotation.x = 0.12 + Math.sin(elapsed * 0.24) * 0.035;
        wireSphere.rotation.y -= 0.0004;
        halo.rotation.z += 0.0007;
        markers.forEach((marker, index) => {
          const pulse = 1 + Math.sin(elapsed * 2.4 + index) * 0.22;
          marker.scale.setScalar(pulse);
          marker.material.opacity = 0.62 + Math.sin(elapsed * 2.1 + index) * 0.25;
        });
        renderer.render(scene, camera);
      };
      frameId = requestAnimationFrame(render);

      return () => {
        cancelAnimationFrame(frameId);
        resizeObserver.disconnect();
        visibilityObserver.disconnect();
        scene.traverse((object) => {
          object.geometry?.dispose?.();
          if (Array.isArray(object.material)) object.material.forEach((material) => material.dispose?.());
          else object.material?.dispose?.();
        });
        renderer.dispose();
        renderer.domElement.remove();
      };
    }, []);

    return <div className="globe-canvas" ref={hostRef} data-scene="globe" aria-hidden="true" />;
  }

  function Navigation() {
    const [open, setOpen] = useState(false);
    const [scrolled, setScrolled] = useState(false);

    useEffect(() => {
      const onScroll = () => setScrolled(window.scrollY > 26);
      onScroll();
      window.addEventListener("scroll", onScroll, { passive: true });
      return () => window.removeEventListener("scroll", onScroll);
    }, []);

    useEffect(() => {
      const onResize = () => {
        if (window.innerWidth > 860) setOpen(false);
      };
      window.addEventListener("resize", onResize);
      return () => window.removeEventListener("resize", onResize);
    }, []);

    return (
      <div className="site-nav-wrap">
        <nav className="site-nav" data-scrolled={scrolled ? "true" : "false"} aria-label="主导航">
          <a className="nav-brand" href="#top" aria-label="返回首页顶部">
            <span className="brand-symbol">T⌁</span>
            <span>
              TELEVAULT
              <small>Media Intelligence</small>
            </span>
          </a>

          <div className="nav-links">
            {navLinks.map((item) => <a href={item.href} key={item.href}>{item.label}</a>)}
          </div>

          <div className="nav-actions">
            <span className="nav-status"><i /> SYSTEM ONLINE</span>
            <a className="button-primary" href="/dashboard">
              开始体验 <Icon name="arrow" />
            </a>
            <button
              className="mobile-menu-button"
              type="button"
              aria-label={open ? "关闭导航菜单" : "打开导航菜单"}
              aria-expanded={open}
              onClick={() => setOpen((value) => !value)}
            >
              <span />
            </button>
          </div>
        </nav>

        <AnimatePresence>
          {open && (
            <motion.div
              className="mobile-menu"
              initial={{ opacity: 0, y: -12, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -8, scale: 0.98 }}
              transition={{ duration: 0.24, ease: [0.16, 1, 0.3, 1] }}
            >
              {navLinks.map((item) => (
                <a href={item.href} key={item.href} onClick={() => setOpen(false)}>{item.label}</a>
              ))}
              <a href="/dashboard" onClick={() => setOpen(false)}>进入工作台 →</a>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    );
  }

  function Hero() {
    const scrollToShowcase = (event) => {
      event.preventDefault();
      document.querySelector("#interface")?.scrollIntoView({ behavior: "smooth", block: "start" });
    };

    return (
      <header className="hero" id="top">
        <HeroScene />
        <div className="hero-inner">
          <motion.div
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.9, delay: 0.12, ease: [0.16, 1, 0.3, 1] }}
          >
            <div className="hero-eyebrow"><b>NEW</b> PRIVATE MEDIA INFRASTRUCTURE · {appVersion}</div>
          </motion.div>

          <motion.h1
            initial={{ opacity: 0, y: 28, filter: "blur(12px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            transition={{ duration: 1.15, delay: 0.2, ease: [0.16, 1, 0.3, 1] }}
          >
            把 Telegram
            <span className="gradient-text">变成你的私有媒体云。</span>
          </motion.h1>

          <motion.p
            className="hero-subtitle"
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.9, delay: 0.42, ease: [0.16, 1, 0.3, 1] }}
          >
            一个为深度内容收藏者打造的自托管媒体基础设施。下载、筛选、转发与恢复，在同一个克制而强大的界面内完成。
          </motion.p>

          <motion.div
            className="hero-actions"
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.56, ease: [0.16, 1, 0.3, 1] }}
          >
            <a className="button-primary" href="/dashboard">
              进入工作台 <Icon name="arrow" />
            </a>
            <a className="button-secondary" href="#interface" onClick={scrollToShowcase}>
              查看产品界面 <Icon name="external" />
            </a>
          </motion.div>

          <motion.div
            className="hero-proof"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 1.2, delay: 0.8 }}
          >
            <div className="hero-proof-item"><strong>10×</strong><span>并行下载任务</span></div>
            <div className="hero-proof-item"><strong>∞</strong><span>断点续传恢复</span></div>
            <div className="hero-proof-item"><strong>LOCAL</strong><span>数据始终在你手中</span></div>
          </motion.div>
        </div>

        <span className="hero-corner left">SELF HOSTED · ENCRYPTED · LOCAL FIRST</span>
        <span className="hero-corner right">TELEGRAM MEDIA DOWNLOADER · 2026</span>
        <a className="scroll-cue" href="#product" aria-label="向下浏览产品能力"><i /></a>
      </header>
    );
  }

  function TrustStrip() {
    const items = [
      ["01", "TELEGRAM NATIVE"],
      ["02", "LOCAL STORAGE"],
      ["03", "RESUMABLE STREAM"],
      ["04", "AUTOMATION READY"],
      ["05", "OPEN SOURCE"],
      ["06", "DOCKER DEPLOY"],
    ];
    const repeated = [...items, ...items];
    return (
      <div className="trust-strip" aria-label="产品能力概览">
        <div className="trust-track">
          {repeated.map((item, index) => (
            <span className="trust-item" key={item[1] + index}><i>{item[0]}</i>{item[1]}</span>
          ))}
        </div>
      </div>
    );
  }

  function FeatureCard({ feature, index }) {
    const cardRef = useRef(null);

    useEffect(() => {
      const card = cardRef.current;
      if (!card || !window.gsap) return undefined;
      const tween = window.gsap.fromTo(
        card,
        { y: 54, opacity: 0 },
        {
          y: 0,
          opacity: 1,
          duration: 1,
          delay: index * 0.11,
          ease: "power3.out",
          scrollTrigger: { trigger: card, start: "top 86%", once: true },
        }
      );
      return () => {
        tween.scrollTrigger?.kill();
        tween.kill();
      };
    }, [index]);

    const handlePointerMove = (event) => {
      const card = cardRef.current;
      if (!card) return;
      const rect = card.getBoundingClientRect();
      card.style.setProperty("--spot-x", ((event.clientX - rect.left) / rect.width) * 100 + "%");
      card.style.setProperty("--spot-y", ((event.clientY - rect.top) / rect.height) * 100 + "%");
    };

    return (
      <article className="feature-card" ref={cardRef} onPointerMove={handlePointerMove}>
        <div className="feature-topline"><span>{feature.label}</span><span className="feature-index">{feature.index} / 03</span></div>
        <div className="feature-stage"><FeatureModel type={feature.type} /></div>
        <h3>{feature.title}</h3>
        <p>{feature.body}</p>
        <div className="feature-meta">{feature.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
      </article>
    );
  }

  function Features() {
    return (
      <section className="section features" id="product">
        <div className="container">
          <motion.div
            className="section-heading"
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.35 }}
            transition={{ duration: 0.85, ease: [0.16, 1, 0.3, 1] }}
          >
            <div className="section-kicker">Core capabilities</div>
            <h2>安静运行的基础设施，<br />不只是一种下载方式。</h2>
            <p>将 Telegram 的信息流变成可检索、可筛选、可长期保存的本地资产。每一项能力都为稳定性与掌控感服务。</p>
          </motion.div>
          <div className="feature-grid">
            {features.map((feature, index) => <FeatureCard feature={feature} index={index} key={feature.title} />)}
          </div>
        </div>
      </section>
    );
  }

  function NetworkSection() {
    return (
      <section className="section globe-section" id="network">
        <div className="container globe-layout">
          <div className="globe-copy">
            <motion.div
              className="section-heading"
              initial={{ opacity: 0, y: 28 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.35 }}
              transition={{ duration: 0.85, ease: [0.16, 1, 0.3, 1] }}
            >
              <div className="section-kicker">Data network</div>
              <h2>从任意频道，<br />连接到你的内容网络。</h2>
              <p>一套始终在线的内容基础设施。跨频道下载、消息补转与规则监听共享同一个任务队列，运行状态实时可见。</p>
            </motion.div>

            <div className="globe-stat-list">
              {showcaseStats.map((item, index) => (
                <motion.div
                  className="globe-stat"
                  key={item.index}
                  initial={{ opacity: 0, x: -22 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true, amount: 0.6 }}
                  transition={{ duration: 0.65, delay: index * 0.08, ease: [0.16, 1, 0.3, 1] }}
                >
                  <span>{item.index}</span>
                  <div><strong>{item.title}</strong><p>{item.body}</p></div>
                </motion.div>
              ))}
            </div>

            <a className="button-secondary" href="/dashboard">查看实时网络 <Icon name="arrow" /></a>
          </div>

          <motion.div
            className="globe-visual"
            initial={{ opacity: 0, scale: 0.9 }}
            whileInView={{ opacity: 1, scale: 1 }}
            viewport={{ once: true, amount: 0.35 }}
            transition={{ duration: 1.1, ease: [0.16, 1, 0.3, 1] }}
          >
            <GlobeScene />
            <span className="globe-ring" />
            <span className="globe-ring" />
            <div className="globe-badge badge-one"><i /><span>ACTIVE NODES<br /><b>7 regions</b></span></div>
            <div className="globe-badge badge-two"><span>TRANSFER<br /><b>encrypted</b></span><i /></div>
            <div className="globe-badge badge-three"><i /><span>SYNC STATE<br /><b>real-time</b></span></div>
          </motion.div>
        </div>
      </section>
    );
  }

  function Showcase({ onOpen }) {
    return (
      <section className="section showcase" id="interface">
        <div className="container">
          <div className="showcase-heading">
            <motion.div
              className="section-heading"
              initial={{ opacity: 0, y: 28 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.35 }}
              transition={{ duration: 0.85, ease: [0.16, 1, 0.3, 1] }}
            >
              <div className="section-kicker">Product interface</div>
              <h2>真实界面，经得起使用。</h2>
              <p>不是概念稿。每一个屏幕都来自运行中的产品，信息密度与留白之间保持精确平衡。</p>
            </motion.div>
            <div className="showcase-note"><i>↗</i>点击画面进入原尺寸预览。截图取自实际运行界面。</div>
          </div>

          <div className="screenshot-grid">
            {screenshots.map((item, index) => (
              <motion.button
                type="button"
                className="screenshot-card"
                key={item.title}
                onClick={() => onOpen(index)}
                initial={{ opacity: 0, y: 36 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, amount: 0.2 }}
                transition={{ duration: 0.8, delay: index * 0.08, ease: [0.16, 1, 0.3, 1] }}
                aria-label={"放大查看：" + item.title}
              >
                <img src={asset(item.src)} alt={item.alt} loading={index === 0 ? "eager" : "lazy"} decoding="async" />
                <span className="screenshot-caption">
                  <span><strong>{item.title}</strong><span>{item.meta}</span></span>
                  <i>↗</i>
                </span>
              </motion.button>
            ))}
          </div>
        </div>
      </section>
    );
  }

  function BentoSection() {
    const cards = [
      { icon: "shield", title: "本地加密边界", body: "API Hash、Bot Token 与代理凭据使用本地密钥加密，不向无关服务暴露。", number: "01", className: "b1" },
      { icon: "layers", title: "一个队列，所有来源", body: "频道内下载、跨频道任务与 Bot 请求共享并发控制、失败重试与状态推送。", number: "02", className: "b2" },
      { icon: "bolt", title: "失败自动恢复", body: "指数退避、刷新文件引用和分块续传，让长任务在网络波动中继续前进。", number: "03", className: "b3" },
      { icon: "radar", title: "规则级监听", body: "按消息 ID、日期、大小、分辨率、时长与关键词组合过滤，精准捕获目标内容。", number: "04", className: "b4" },
      { icon: "spark", title: "为长期使用设计", body: "数据库备份、历史记录、分页与持久化缓存，让收藏成为可以托付的长期系统。", number: "05", className: "b5" },
    ];

    return (
      <section className="section">
        <div className="container">
          <motion.div
            className="section-heading"
            initial={{ opacity: 0, y: 28 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.35 }}
            transition={{ duration: 0.85, ease: [0.16, 1, 0.3, 1] }}
          >
            <div className="section-kicker">Built for permanence</div>
            <h2>掌控感，来自每个细节。</h2>
            <p>不只是把文件下载下来，而是建立一套可持续、可恢复、可观察的媒体工作流。</p>
          </motion.div>

          <div className="bento">
            {cards.map((card, index) => (
              <motion.article
                className={"bento-card " + card.className}
                key={card.title}
                initial={{ opacity: 0, y: 28 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, amount: 0.28 }}
                transition={{ duration: 0.65, delay: (index % 3) * 0.06, ease: [0.16, 1, 0.3, 1] }}
              >
                <div className="bento-icon"><Icon name={card.icon} /></div>
                <h3>{card.title}</h3>
                <p>{card.body}</p>
                <span className="bento-number">{card.number}</span>
              </motion.article>
            ))}
          </div>
        </div>
      </section>
    );
  }

  function FAQ() {
    const [openIndex, setOpenIndex] = useState(0);
    return (
      <section className="section faq-section" id="faq">
        <div className="container faq-layout">
          <motion.div
            className="faq-intro"
            initial={{ opacity: 0, y: 25 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.4 }}
            transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
          >
            <div className="section-kicker">Answers</div>
            <h2>需要知道的，<br />都在这里。</h2>
            <p>关于隐私、部署与下载机制的高频问题。如果还需要更多信息，可以查看 GitHub 上的完整文档。</p>
          </motion.div>

          <div className="faq-list">
            {faqs.map((item, index) => {
              const isOpen = openIndex === index;
              return (
                <div className="faq-item" data-open={isOpen ? "true" : "false"} key={item.question}>
                  <button
                    className="faq-question"
                    type="button"
                    aria-expanded={isOpen}
                    aria-controls={"faq-answer-" + index}
                    onClick={() => setOpenIndex(isOpen ? -1 : index)}
                  >
                    <span>{item.question}</span><span>+</span>
                  </button>
                  <AnimatePresence initial={false}>
                    {isOpen && (
                      <motion.div
                        className="faq-answer"
                        id={"faq-answer-" + index}
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
                      >
                        <p>{item.answer}</p>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              );
            })}
          </div>
        </div>
      </section>
    );
  }

  function Footer() {
    return (
      <footer className="footer">
        <div className="container">
          <div className="footer-grid">
            <div className="footer-brand">
              <a className="nav-brand" href="#top">
                <span className="brand-symbol">T⌁</span>
                <span>TELEVAULT<small>Media Intelligence</small></span>
              </a>
              <p>把 Telegram 内容带回你的掌控。自托管、可恢复、面向长期使用的媒体基础设施。</p>
              <span className="footer-status"><i /> SELF HOSTED · v{appVersion}</span>
            </div>

            <div className="footer-column">
              <h3>产品</h3>
              <a href="#product">核心能力</a>
              <a href="#network">数据网络</a>
              <a href="#interface">真实界面</a>
            </div>
            <div className="footer-column">
              <h3>开始</h3>
              <a href="/dashboard">进入工作台</a>
              <a href="/setup">配置向导</a>
              <a href="/api/docs">API 文档</a>
            </div>
            <div className="footer-column">
              <h3>项目</h3>
              <a href="https://github.com/cvpia/telegram-media-downloader-web" target="_blank" rel="noreferrer">GitHub ↗</a>
              <a href="https://github.com/cvpia/telegram-media-downloader-web/releases" target="_blank" rel="noreferrer">Release Notes ↗</a>
              <a href="https://github.com/cvpia/telegram-media-downloader-web/blob/master/LICENSE" target="_blank" rel="noreferrer">License ↗</a>
            </div>
          </div>
          <div className="footer-wordmark" aria-hidden="true">TELEVAULT</div>
          <div className="footer-bottom">
            <span>© 2026 TeleVault / Telegram Media Downloader. Built for private media.</span>
            <span>Made with <a href="https://threejs.org" target="_blank" rel="noreferrer">Three.js</a> · <a href="https://gsap.com" target="_blank" rel="noreferrer">GSAP</a> · <a href="https://www.framer.com/motion/" target="_blank" rel="noreferrer">Motion</a></span>
          </div>
        </div>
      </footer>
    );
  }

  function FinalCTA() {
    return (
      <section className="final-cta">
        <motion.div
          initial={{ opacity: 0, y: 28 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.45 }}
          transition={{ duration: 0.85, ease: [0.16, 1, 0.3, 1] }}
        >
          <div className="section-kicker">Start the signal</div>
          <h2>你的内容，<br />值得一个更好的归处。</h2>
          <p>打开工作台，完成一次配置。之后，让内容自己流向该去的地方。</p>
          <div className="hero-actions">
            <a className="button-primary" href="/dashboard">立即开始 <Icon name="arrow" /></a>
            <a className="button-secondary" href="https://github.com/cvpia/telegram-media-downloader-web" target="_blank" rel="noreferrer">查看 GitHub <Icon name="external" /></a>
          </div>
        </motion.div>
      </section>
    );
  }

  function ScreenshotLightbox({ index, onClose }) {
    useEffect(() => {
      if (index === null) return undefined;
      const onKeyDown = (event) => {
        if (event.key === "Escape") onClose();
      };
      document.body.style.overflow = "hidden";
      window.addEventListener("keydown", onKeyDown);
      return () => {
        document.body.style.overflow = "";
        window.removeEventListener("keydown", onKeyDown);
      };
    }, [index, onClose]);

    return (
      <AnimatePresence>
        {index !== null && (
          <motion.div
            className="lightbox"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            role="dialog"
            aria-modal="true"
            aria-label={screenshots[index].title}
          >
            <motion.div
              className="lightbox-inner"
              initial={{ opacity: 0, scale: 0.94, y: 18 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: 12 }}
              transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
              onClick={(event) => event.stopPropagation()}
            >
              <button className="lightbox-close" type="button" onClick={onClose} aria-label="关闭预览">×</button>
              <img src={asset(screenshots[index].src)} alt={screenshots[index].alt} />
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    );
  }

  function ScrollProgress() {
    const [progress, setProgress] = useState(0);
    useEffect(() => {
      const update = () => {
        const max = document.documentElement.scrollHeight - window.innerHeight;
        setProgress(max > 0 ? window.scrollY / max : 0);
      };
      update();
      window.addEventListener("scroll", update, { passive: true });
      return () => window.removeEventListener("scroll", update);
    }, []);
    return <div className="scroll-progress" style={{ transform: "scaleX(" + progress + ")" }} aria-hidden="true" />;
  }

  function App() {
    const [lightboxIndex, setLightboxIndex] = useState(null);
    const rootRef = useRef(null);
    const auraRef = useRef(null);

    useEffect(() => {
      if (!window.gsap) return undefined;
      const gsap = window.gsap;
      if (window.ScrollTrigger) gsap.registerPlugin(window.ScrollTrigger);

      const context = gsap.context(() => {
        gsap.to(".hero-inner", {
          y: 82,
          opacity: 0.4,
          ease: "none",
          scrollTrigger: {
            trigger: ".hero",
            start: "top top",
            end: "bottom top",
            scrub: 1.2,
          },
        });

        gsap.to(".hero-canvas", {
          scale: 1.1,
          opacity: 0.45,
          ease: "none",
          scrollTrigger: {
            trigger: ".hero",
            start: "top top",
            end: "bottom top",
            scrub: 1.4,
          },
        });

        if (window.matchMedia("(min-width: 861px)").matches) {
          gsap.to(".globe-visual", {
            y: -48,
            ease: "none",
            scrollTrigger: {
              trigger: ".globe-section",
              start: "top bottom",
              end: "bottom top",
              scrub: 1.5,
            },
          });
        }
      }, rootRef);

      return () => context.revert();
    }, []);

    useEffect(() => {
      let frame = 0;
      let targetX = window.innerWidth / 2;
      let targetY = window.innerHeight / 2;
      let currentX = targetX;
      let currentY = targetY;

      const onPointer = (event) => {
        targetX = event.clientX;
        targetY = event.clientY;
        if (!frame) frame = requestAnimationFrame(animateAura);
      };

      const animateAura = () => {
        currentX += (targetX - currentX) * 0.11;
        currentY += (targetY - currentY) * 0.11;
        if (auraRef.current) {
          auraRef.current.style.left = currentX + "px";
          auraRef.current.style.top = currentY + "px";
        }
        if (Math.abs(targetX - currentX) > 0.2 || Math.abs(targetY - currentY) > 0.2) {
          frame = requestAnimationFrame(animateAura);
        } else {
          frame = 0;
        }
      };

      window.addEventListener("pointermove", onPointer, { passive: true });
      return () => {
        window.removeEventListener("pointermove", onPointer);
        cancelAnimationFrame(frame);
      };
    }, []);

    return (
      <div className="landing-app" ref={rootRef}>
        <ScrollProgress />
        <div className="pointer-aura" ref={auraRef} aria-hidden="true" />
        <Navigation />
        <main>
          <Hero />
          <TrustStrip />
          <Features />
          <NetworkSection />
          <Showcase onOpen={setLightboxIndex} />
          <BentoSection />
          <FAQ />
          <FinalCTA />
        </main>
        <Footer />
        <ScreenshotLightbox index={lightboxIndex} onClose={() => setLightboxIndex(null)} />
      </div>
    );
  }

  const rootElement = document.getElementById("root");
  if (rootElement) {
    ReactDOM.createRoot(rootElement).render(<App />);
  }
})();
