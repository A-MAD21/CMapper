(() => {
    const ICON_SIZE = 16;

    const buildSvg = (name) => {
        const label = (name || '').slice(0, 1).toUpperCase();
        return `
            <svg xmlns="http://www.w3.org/2000/svg" width="${ICON_SIZE}" height="${ICON_SIZE}"
                 viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"
                 stroke-linecap="round" stroke-linejoin="round"
                 style="width: 1em; height: 1em; vertical-align: -0.125em;">
                <circle cx="8" cy="8" r="6"></circle>
                <text x="8" y="11" text-anchor="middle" font-size="7"
                      fill="currentColor" stroke="none" font-family="Arial, sans-serif">
                    ${label}
                </text>
                <title>${name || ''}</title>
            </svg>
        `;
    };

    const replace = () => {
        document.querySelectorAll('[data-feather]').forEach((el) => {
            const name = el.getAttribute('data-feather') || '';
            el.outerHTML = buildSvg(name);
        });
    };

    window.feather = { replace };
})();
