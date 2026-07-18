document.addEventListener('DOMContentLoaded', () => {
  initThemeToggle();
  initMobileMenu();
  initTabs();
  initActiveNavOnScroll();
  initSearch();
  initCalculator();
});

/* Theme Toggle Functionality */
function initThemeToggle() {
  const themeToggleBtn = document.getElementById('theme-toggle');
  const body = document.body;
  
  // Check local storage for preference
  const savedTheme = localStorage.getItem('theme');
  if (savedTheme === 'light') {
    body.classList.add('light-theme');
    updateThemeIcon('light');
  } else {
    body.classList.remove('light-theme');
    updateThemeIcon('dark');
  }

  themeToggleBtn.addEventListener('click', () => {
    body.classList.toggle('light-theme');
    const currentTheme = body.classList.contains('light-theme') ? 'light' : 'dark';
    localStorage.setItem('theme', currentTheme);
    updateThemeIcon(currentTheme);
  });
}

function updateThemeIcon(theme) {
  const btn = document.getElementById('theme-toggle');
  if (theme === 'light') {
    btn.innerHTML = `
      <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 3a6.8 6.8 0 0 0 9 9 9 9 0 1 1-9-9Z"/>
      </svg>
    `;
    btn.title = "Switch to Dark Mode";
  } else {
    btn.innerHTML = `
      <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="4"/>
        <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>
      </svg>
    `;
    btn.title = "Switch to Light Mode";
  }
}

/* Mobile Sidebar Toggle */
function initMobileMenu() {
  const menuToggle = document.getElementById('menu-toggle');
  const sidebar = document.querySelector('.sidebar');
  const mainContent = document.querySelector('.main-content');

  if (menuToggle && sidebar) {
    menuToggle.addEventListener('click', (e) => {
      e.stopPropagation();
      sidebar.classList.toggle('open');
    });

    // Close sidebar when clicking content
    mainContent.addEventListener('click', () => {
      if (sidebar.classList.contains('open')) {
        sidebar.classList.remove('open');
      }
    });

    // Close sidebar on link click (mobile nav)
    const links = sidebar.querySelectorAll('.nav-link');
    links.forEach(link => {
      link.addEventListener('click', () => {
        if (window.innerWidth <= 768) {
          sidebar.classList.remove('open');
        }
      });
    });
  }
}

/* Tab Component Setup */
function initTabs() {
  const containers = document.querySelectorAll('.tabs-container');
  containers.forEach(container => {
    const buttons = container.querySelectorAll('.tab-btn');
    const panes = container.querySelectorAll('.tab-pane');

    buttons.forEach(btn => {
      btn.addEventListener('click', () => {
        const target = btn.dataset.tab;

        buttons.forEach(b => b.classList.remove('active'));
        panes.forEach(p => p.classList.remove('active'));

        btn.classList.add('active');
        container.querySelector(`.tab-pane[data-pane="${target}"]`).classList.add('active');
      });
    });
  });
}

/* Highlight Active Sidebar Link on Scroll */
function initActiveNavOnScroll() {
  const sections = document.querySelectorAll('.doc-section');
  const navLinks = document.querySelectorAll('.sidebar-nav .nav-link');

  window.addEventListener('scroll', () => {
    let current = '';
    const scrollPos = window.scrollY + 100; // Offset

    sections.forEach(section => {
      const top = section.offsetTop;
      const height = section.offsetHeight;
      if (scrollPos >= top && scrollPos < top + height) {
        current = section.getAttribute('id');
      }
    });

    if (current) {
      navLinks.forEach(link => {
        link.classList.remove('active');
        const href = link.getAttribute('href');
        if (href && href.substring(1) === current) {
          link.classList.add('active');
        }
      });
    }
  });
}

/* Search Bar (Local Header Filtering) */
function initSearch() {
  const searchInput = document.getElementById('doc-search');
  const navLinks = document.querySelectorAll('.sidebar-nav .nav-link');
  const navGroups = document.querySelectorAll('.sidebar-nav .nav-group');

  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      const term = e.target.value.toLowerCase().trim();

      if (term === '') {
        // Show everything
        navLinks.forEach(link => link.style.display = 'flex');
        navGroups.forEach(group => group.style.display = 'block');
        return;
      }

      navGroups.forEach(group => {
        let hasVisibleLink = false;
        const links = group.querySelectorAll('.nav-link');
        
        links.forEach(link => {
          const text = link.textContent.toLowerCase();
          if (text.includes(term)) {
            link.style.display = 'flex';
            hasVisibleLink = true;
          } else {
            link.style.display = 'none';
          }
        });

        // Hide the whole group if it has no matching links
        if (hasVisibleLink) {
          group.style.display = 'block';
        } else {
          group.style.display = 'none';
        }
      });
    });
  }
}

/* Dynamic Interactive Cost Calculator */
function initCalculator() {
  const ordersSlider = document.getElementById('orders-slider');
  const ordersVal = document.getElementById('orders-val');
  
  const msgsSlider = document.getElementById('msgs-slider');
  const msgsVal = document.getElementById('msgs-val');
  
  const llmSelect = document.getElementById('calc-llm');
  const infraSelect = document.getElementById('calc-infra');

  // Outputs
  const totalDisplay = document.getElementById('calc-total');
  const itemWaba = document.getElementById('item-waba');
  const itemLlm = document.getElementById('item-llm');
  const itemHost = document.getElementById('item-host');
  const itemDb = document.getElementById('item-db');
  const itemOther = document.getElementById('item-other');

  if (!ordersSlider || !totalDisplay) return;

  function calculate() {
    const orders = parseInt(ordersSlider.value);
    const msgsPerOrder = parseInt(msgsSlider.value);
    const llm = llmSelect.value;
    const infra = infraSelect.value;

    ordersVal.textContent = orders;
    msgsVal.textContent = msgsPerOrder;

    const monthlyOrders = orders * 30;

    // 1. WhatsApp Cloud API Pricing (India Region reference as typical, and adjusted for USD exchange rate)
    // - Customer Conversations (User-Initiated): First 1,000 free per month, then ~$0.005 each.
    // - Utility Conversations (Business-Initiated, like notifications to Chefs and Drivers): ~$0.011 each.
    // Let's assume on average, for every order:
    // - 1 User-Initiated conversation (customer order details)
    // - 1.5 Utility conversations (Chef dispatch updates, driver dispatch alerts)
    const userConversations = monthlyOrders;
    const utilityConversations = Math.ceil(monthlyOrders * 1.5);

    const userConvCost = Math.max(0, userConversations - 1000) * 0.005;
    const utilityConvCost = utilityConversations * 0.011;
    const wabaCost = userConvCost + utilityConvCost;

    // 2. LLM Tokens Cost Calculation
    // Average Input: 700 tokens per message (system instructions + current context)
    // Average Output: 250 tokens per message (structured JSON output or text response)
    const inputTokensPerMsg = 700;
    const outputTokensPerMsg = 250;
    const monthlyTotalMessages = monthlyOrders * msgsPerOrder;
    const inputTokens = monthlyTotalMessages * inputTokensPerMsg;
    const outputTokens = monthlyTotalMessages * outputTokensPerMsg;

    let inputRate = 0; // per 1,000,000 tokens
    let outputRate = 0; // per 1,000,000 tokens

    if (llm === 'gemini') {
      // Gemini 1.5 Flash pricing
      inputRate = 0.075;
      outputRate = 0.30;
    } else if (llm === 'gpt4omini') {
      // GPT-4o-mini pricing
      inputRate = 0.150;
      outputRate = 0.60;
    } else if (llm === 'claude') {
      // Claude 3.5 Sonnet pricing
      inputRate = 3.00;
      outputRate = 15.00;
    } else if (llm === 'llama3') {
      // Hosted open source Llama 3 via Groq/Ollama (Groq Rate: Llama 3 8b is $0.05 / $0.08)
      inputRate = 0.05;
      outputRate = 0.08;
    }

    const llmCost = ((inputTokens / 1000000) * inputRate) + ((outputTokens / 1000000) * outputRate);

    // 3. Infrastructure & Hosting
    let hostingCost = 0;
    let dbCost = 0;
    let otherCost = 5.00; // SSL, domain name amortization, remote backups storage (S3)

    if (infra === 'hostinger') {
      // Hostinger VPS scaling
      if (orders <= 100) {
        hostingCost = 5.99; // KVM1 (1 vCPU, 2GB RAM, 50GB NVMe)
        dbCost = 0; // Local Postgres in docker container
      } else if (orders <= 500) {
        hostingCost = 9.99; // KVM2 (2 vCPU, 4GB RAM, 100GB NVMe)
        dbCost = 0; 
      } else {
        hostingCost = 19.99; // KVM4 (4 vCPU, 8GB RAM, 200GB NVMe)
        dbCost = 0; 
      }
      otherCost = 5.00;
    } else if (infra === 'aws') {
      // AWS Setup (Managed ECS, RDS, ALB, NAT Gateway, Backups)
      if (orders <= 100) {
        hostingCost = 35.00; // t3.medium EC2, Application Load Balancer share
        dbCost = 25.00; // db.t3.micro managed RDS
        otherCost = 15.00; // NAT gateway hourly charges, Cloudwatch, Route53
      } else if (orders <= 500) {
        hostingCost = 70.00; // 2x t3.medium (high availability), ALB
        dbCost = 45.00; // db.t3.small managed RDS
        otherCost = 25.00;
      } else {
        hostingCost = 130.00; // t3.large cluster
        dbCost = 90.00; // db.t3.medium production grade
        otherCost = 40.00;
      }
    } else if (infra === 'paas') {
      // Railway / Render / Fly.io (Platform as a Service)
      if (orders <= 100) {
        hostingCost = 12.00; // Shared CPU, 1GB memory
        dbCost = 7.00; // Managed Postgres addon (512MB RAM)
        otherCost = 5.00;
      } else if (orders <= 500) {
        hostingCost = 28.00; // 2GB memory web dyno
        dbCost = 20.00; // 1GB memory postgres
        otherCost = 7.00;
      } else {
        hostingCost = 65.00; 
        dbCost = 50.00; 
        otherCost = 15.00;
      }
    }

    const totalCost = wabaCost + llmCost + hostingCost + dbCost + otherCost;

    // Render results
    totalDisplay.textContent = `$${totalCost.toFixed(2)}`;
    itemWaba.textContent = `$${wabaCost.toFixed(2)}`;
    itemLlm.textContent = `$${llmCost.toFixed(2)}`;
    itemHost.textContent = `$${hostingCost.toFixed(2)}`;
    itemDb.textContent = `$${dbCost.toFixed(2)}`;
    itemOther.textContent = `$${otherCost.toFixed(2)}`;
  }

  // Register Event Listeners
  ordersSlider.addEventListener('input', calculate);
  msgsSlider.addEventListener('input', calculate);
  llmSelect.addEventListener('change', calculate);
  infraSelect.addEventListener('change', calculate);

  // Initial Run
  calculate();
}
