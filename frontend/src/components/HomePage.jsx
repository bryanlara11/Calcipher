import { BrandLogo, Icon } from "./Brand.jsx";

export default function HomePage({ headingRef }) {
  return <div className="home-page">
    <section className="home-hero">
      <div><h1 ref={headingRef} tabIndex={-1}>Find food that fits your day.</h1>
        <p className="page-lead">Explore nearby restaurants, compare menu nutrition and save the places you want to try.</p>
        <div className="home-actions"><a className="button-primary" href="#/search">Find a meal<Icon name="arrow" size={18} /></a><a className="button-secondary" href="#/nearby">Explore nearby<Icon name="pin" size={18} /></a></div>
      </div><BrandLogo className="home-logo" />
    </section>
    <section className="how-it-works" aria-labelledby="how-heading"><h2 id="how-heading">How it works</h2>
      <ol className="how-grid">
        <li><h3>Pick a place</h3><p>Use the map to see restaurants around you.</p></li>
        <li><h3>Look through the menu</h3><p>Search for food and check its calories and source.</p></li>
        <li><h3>Save your next meal</h3><p>Keep a favorite restaurant or add a dish to your plan.</p></li>
      </ol>
    </section>
    <section className="home-data-note"><img src="/brand/approved-seal.webp" alt="Example CalCipher community pick seal" width="120" height="90" />
      <div><h2>What the badge means</h2><p>A Community pick has at least three healthy-choice ratings averaging four stars or higher. It reflects people’s opinions; the nutrition source has its own review status. Meals without enough ratings have no badge.</p></div>
    </section>
    <section className="home-data-link"><div><h2>About menu data</h2><p>See what is in the catalog, how records are reviewed and where nutrition values come from.</p></div><a href="#/data" className="button-secondary">Read about the data<Icon name="arrow" size={17} /></a></section>
  </div>;
}
