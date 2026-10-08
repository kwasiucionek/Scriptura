document.querySelectorAll(".mainnav").forEach((nav) => {
  nav.addEventListener("focusin", (event) => {
    const link = event.target.closest("a");
    if (!link || !nav.contains(link)) return;

    // Natywny Tab potrafi pozostawić częściowo widoczny link bez przewinięcia.
    link.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
  });
});
