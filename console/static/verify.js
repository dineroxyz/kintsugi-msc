// "Check it yourself": verifies a certificate in the visitor's browser.
// 1. SHA-256 of the certificate's exact bytes equals its published ID.
// 2. Its prev_hash equals the SHA-256 of the previous certificate's bytes.
// 3. Its Ed25519 signature verifies under the published public key, or,
//    for certificates issued before the key existed, its ID is in the
//    signed attestation and that attestation's signature verifies.
// Every input is fetched as raw data; every check is computed here.
import * as ed from "/static/noble-ed25519.js";   // MIT, vendored: works where WebCrypto lacks Ed25519

(function () {
  "use strict";
  var box = document.querySelector("[data-verify]");
  if (!box) return;
  var out = box.querySelector(".verify-out"), go = box.querySelector("[data-verify-go]");
  var seq = parseInt(box.dataset.verify, 10);
  var base = box.dataset.base || "/certificates";

  function hex(buf) {
    return Array.prototype.map.call(new Uint8Array(buf), function (b) {
      return b.toString(16).padStart(2, "0"); }).join("");
  }
  function b64(s) { return Uint8Array.from(atob(s), function (c) { return c.charCodeAt(0); }); }
  function sha256(bytes) { return crypto.subtle.digest("SHA-256", bytes).then(hex); }
  function bytesOf(n) {
    return fetch(base + "/" + n + "/canonical").then(function (r) {
      if (!r.ok) throw new Error("certificate " + n + " not found"); return r.arrayBuffer(); });
  }
  function json(url) { return fetch(url).then(function (r) { if (!r.ok) throw new Error(url); return r.json(); }); }
  function line(ok, text) {
    var li = document.createElement("li");
    li.className = ok === null ? "" : (ok ? "pass" : "fail");
    li.textContent = text; out.appendChild(li); return ok;
  }
  async function verifyEd25519(keyB64, message, sigB64) {
    try {                               // the browser's own implementation first
      var key = await crypto.subtle.importKey("raw", b64(keyB64), { name: "Ed25519" }, false, ["verify"]);
      return await crypto.subtle.verify({ name: "Ed25519" }, key, b64(sigB64), message);
    } catch (e) {
      try { return await ed.verifyAsync(b64(sigB64), message, b64(keyB64)); }
      catch (e2) { return null; }       // neither could run the check
    }
  }
  function canonicalAttestation(a) {   // matches the server's sorted, compact JSON
    return new TextEncoder().encode(JSON.stringify({ at: a.at, chain: a.chain, hashes: a.hashes }));
  }

  go.addEventListener("click", async function () {
    go.disabled = true; out.hidden = false; out.textContent = "";
    var all = true;
    try {
      var meta = await json(base + "/" + seq + ".json");
      var bytes = await bytesOf(seq);
      var got = await sha256(bytes);
      all = line(got === meta.hash, "Fingerprint recomputed: " + got.slice(0, 16) +
        (got === meta.hash ? " matches the certificate's ID." : " does NOT match " + meta.hash.slice(0, 16) + ".")) && all;
      var body = JSON.parse(new TextDecoder().decode(bytes));
      if (seq > 0) {
        var prev = await sha256(await bytesOf(seq - 1));
        all = line(prev === body.prev_hash, prev === body.prev_hash ?
          "Linked: it records the exact fingerprint of certificate " + String(seq - 1).padStart(6, "0") + "." :
          "Chain broken: the previous certificate's fingerprint does not match.") && all;
      } else {
        line(true, "First certificate in the chain: it links to the genesis value.");
      }
      var published = await json("/.well-known/kintsugi-key.json");
      if (meta.ed25519) {
        var same = meta.ed25519.key === published.key;
        var sig = await verifyEd25519(meta.ed25519.key, new TextEncoder().encode(meta.hash), meta.ed25519.sig);
        if (sig === null) line(null, "Your browser cannot check Ed25519 signatures; try a current Chrome, Safari or Firefox.");
        else all = line(sig && same, sig && same ? "Signature valid under the published key " + published.key_id + "." :
          "Signature does not verify under the published key.") && all;
      } else if (meta.chain === "live") {
        line(null, "This live certificate was issued before public signatures were switched on; " +
          "its keyed-hash signature can only be checked on the server.");
      } else {
        var att = await json("/data/attestation.json");
        var listed = att.hashes.indexOf(meta.hash) >= 0;
        var asig = await verifyEd25519(att.key, canonicalAttestation(att), att.sig);
        all = line(listed, listed ? "Listed in the signed attestation of " + att.count + " study certificates (" + att.at.slice(0, 10) + ")." :
          "Not listed in the signed attestation.") && all;
        if (asig === null) line(null, "Your browser cannot check Ed25519 signatures; try a current Chrome, Safari or Firefox.");
        else all = line(asig && att.key === published.key, asig ? "The attestation's signature is valid under the published key " + published.key_id + "." :
          "The attestation's signature does not verify.") && all;
      }
      var unchecked = out.querySelectorAll("li:not(.pass):not(.fail)").length > 0;
      line(all && !unchecked ? true : (all ? null : false),
           !all ? "Verification failed." :
           unchecked ? "Fingerprint and chain verified; the signature could not be checked in this browser." :
           "Verified. This record is genuine and unchanged.");
    } catch (e) {
      line(false, "Could not complete the check: " + e.message);
    }
    go.disabled = false; go.textContent = "Check again";
  });
})();
