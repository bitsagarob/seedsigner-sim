// Matomo, reached through this site's own origin.
//
// The page's content security policy names exactly one host beyond 'self', and
// the page says so to anyone who opens the panel. An analytics host added to
// that list would make that sentence false, and would be the second thing this
// page ever talks to, which is not a trade worth making for a visit count. So
// nothing here is off-origin: the site proxies /mt.js to the Matomo tracker and
// /mt.php to its collector, and to the browser both of them are this site.
//
// Both sit at the site root rather than under the simulator's own directory,
// because that directory is the service worker's scope. A beacon underneath it
// would be intercepted by the offline cache, stored, and served back from the
// cache the next time -- an analytics request that is answered from disk is a
// request that was never made, and one that is replayed is a visit that never
// happened. At the root they are outside the worker's scope entirely and it
// never sees them.
//
// Every path through here is failure tolerant on purpose, because the simulator
// is the point and this is not. A clone of this repository serves no /mt.js,
// nor does a browser with a blocker in front of it: the script never arrives,
// the queue is dropped on the first error, and nothing else on the page can
// tell the difference. Nothing here is on the wallet's critical path either --
// the calls below only ever append to an array or hand a URL to an image.

(function (scope) {
  "use strict";

  // bitsaga.be's own Matomo site, which is where the simulator's visits land
  // for the moment. The simulator deserves its own site there -- it is a
  // different audience answering a different question, and mixing it into the
  // marketing site's numbers muddies both -- but creating one needs somebody
  // logged into Matomo, which nobody currently is. One constant, so that when
  // that happens this is the only line that changes.
  var SITE_ID = "1";

  var SCRIPT_URL = "/mt.js";     // proxied to analytics.bitsaga.be/matomo.js
  var TRACKER_URL = "/mt.php";   // proxied to analytics.bitsaga.be/matomo.php

  // Set the moment the tracker cannot be had, and never unset. Until matomo.js
  // arrives _paq is a plain array that grows with everything pushed into it,
  // and the one page here that can produce thousands of them is the one where
  // somebody holds down an arrow key. Dropping the queue on the error means a
  // blocked tracker costs a few array entries rather than a leak.
  var dead = false;

  scope._paq = scope._paq || [];

  // Through the global every single time, never through a saved reference to
  // it. _paq starts as a plain array that queues commands, and matomo.js, once
  // it arrives, drains that array and then REPLACES window._paq with an object
  // whose push executes a command instead of storing it. A reference taken
  // before that keeps pointing at the abandoned array: everything appears to
  // work, nothing is ever sent, and the only symptom is a Matomo that reports
  // one pageview per visit and no events at all.
  function push(command) {
    if (dead) return;
    try {
      scope._paq.push(command);
    } catch (error) {
      dead = true;
    }
  }

  // Milestones are once per visit, and a visit here survives a reload: the
  // firmware switch and the tutorial both restart the page by changing the URL,
  // and neither is a second visit. sessionStorage is the tab, which is the
  // closest thing to a visit the page can see; the object beside it is what
  // answers when storage is refused, so a locked-down browser reports each
  // milestone once rather than once per screen.
  var reached = {};

  function once(name) {
    if (reached[name]) return false;
    reached[name] = true;
    try {
      var key = "sim-milestone:" + name;
      if (scope.sessionStorage.getItem(key)) return false;
      scope.sessionStorage.setItem(key, "1");
    } catch (error) {
      /* no storage: the object above is enough to stop it repeating */
    }
    return true;
  }

  scope.Track = {
    /**
     * One click, one line. Category, action, what was clicked, and optionally a
     * number.
     *
     * The number is Matomo's own event value, which it already averages and
     * takes the min and max of per action, in the Events report, with nothing to
     * set up and nobody needing to be logged in to create it first. That is why
     * the boot timings below are carried here rather than in an invented scheme:
     * the report that answers "how long does this take, and for whom" already
     * exists and was simply never given a number to work with.
     */
    event: function (category, action, name, value) {
      var command = ["trackEvent", category, action,
                     name === undefined ? "" : String(name)];
      // Omitted rather than sent as zero when there is nothing to measure: a
      // zero is a data point and would drag every average it landed in.
      if (typeof value === "number" && isFinite(value)) command.push(value);
      push(command);
    },

    /**
     * A screen the device reached, as a pageview of a URL that does not exist.
     *
     * A funnel cannot describe a device menu: it branches, it doubles back, and
     * the interesting question is usually how far somebody got rather than
     * whether they walked a fixed line. Matomo's Users Flow and Transitions
     * reports answer exactly that and both are built on page URLs, so the
     * screens are given URLs.
     */
    screen: function (name) {
      push(["setCustomUrl", "/simulator/screen/" + name]);
      push(["setDocumentTitle", "Simulator screen: " + name]);
      push(["trackPageView"]);
    },

    /**
     * Something that only matters once: a visit either got there or it did not.
     *
     * An event rather than a visit-scoped custom dimension, which is what this
     * would otherwise be. A custom dimension has to be created in Matomo's
     * admin before a single one can be recorded, and nobody can log in there
     * yet; an event needs nothing set up, and a segment on its action answers
     * the same question at visit scope.
     */
    milestone: function (name) {
      if (once(name)) push(["trackEvent", "milestone", name]);
    }
  };

  push(["setTrackerUrl", TRACKER_URL]);
  push(["setSiteId", SITE_ID]);
  push(["enableLinkTracking"]);
  // A visit that loads the page and never gets the wallet up sends exactly one
  // request, and Matomo can only date a visit from the requests it receives, so
  // every one of those was recorded as lasting zero seconds. That is precisely
  // the visit worth understanding -- somebody who waited and left is a different
  // problem from somebody who bounced -- and the difference was invisible. The
  // heartbeat is Matomo's own answer to it and costs one line.
  push(["enableHeartBeatTimer", 15]);
  // The real page, once, before anything below starts overwriting the URL with
  // screens that are not pages.
  push(["trackPageView"]);

  var tag = document.createElement("script");
  tag.async = true;
  tag.src = SCRIPT_URL;
  tag.onerror = function () {
    dead = true;
    // Still the plain array, since the thing that would have replaced it is
    // what just failed to load.
    scope._paq.length = 0;
  };
  document.head.appendChild(tag);
})(typeof self !== "undefined" ? self : this);
