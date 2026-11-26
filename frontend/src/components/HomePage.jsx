import { Icon } from "./Brand.jsx";

export default function HomePage({ headingRef }) {
  return <div className="home-page">
    <section className="home-hero">
      <div><h1 ref={headingRef} tabIndex={-1}>Calorie-counted meals near you</h1>
        <p className="page-lead">Search Metro Manila restaurant menus, see where each calorie figure comes from and plan what to eat.</p>
        <div className="home-actions"><a className="button-primary" href="#/search">Find a meal<Icon name="arrow" size={18} /></a><a className="button-secondary" href="#/nearby">Explore nearby<Icon name="pin" size={18} /></a></div>
      </div><figure className="home-menu-photo"><img src="/media/web-crisostomo-crazy-sisa.webp" alt="Crisostomo Crazy Sisa salad from the restaurant menu" width="687" height="543" decoding="async" /><figcaption>
        <div className="home-photo-title"><span>Crazy Sisa</span><small>Crisostomo · pending review</small></div>
        <p className="home-photo-kcal figure">190<span>kcal</span></p>
        <dl className="home-photo-macros figure"><div><dt>Protein</dt><dd>26 g</dd></div><div><dt>Carbs</dt><dd>15 g</dd></div><div><dt>Fat</dt><dd>3 g</dd></div></dl>
        <a className="source-tag" href="https://www.florabelgroup.com.ph/menu/Crisostomo-MainMenu.pdf#page=14" target="_blank" rel="noreferrer">Source: Crisostomo menu, p. 14 ↗</a>
      </figcaption></figure>
    </section>
    <details className="home-guide"><summary>How it works</summary>
      <ol className="how-grid">
        <li><h3>Pick a place</h3><p>Use the map to see restaurants around you.</p></li>
        <li><h3>Look through the menu</h3><p>Search for food and check its calories and source.</p></li>
        <li><h3>Save your next meal</h3><p>Keep a favorite restaurant or add a dish to your plan.</p></li>
      </ol>
    </details>
    <details className="home-community"><summary><img src="/brand/approved-seal.webp" alt="Example CalCipher community pick seal" width="48" height="36" /><span>About community picks</span></summary><p>Look for this badge on meals rated 4/5 or higher by at least three people. Ratings reflect people’s opinions. Nutrition sources are reviewed separately.</p></details>
    <section className="home-data-link"><div><h2>About menu data</h2><p>See what is in the catalog, how records are reviewed and where nutrition values come from.</p></div><a href="#/data" className="button-secondary">Read about the data<Icon name="arrow" size={17} /></a></section>
  </div>;
}
