use std::collections::HashMap;

/// Path component that matches every key of a map.
pub const WILDCARD: &str = "*";

/// A set of key paths, stored as a trie, that restricts which parts of a parsed
/// save are converted into Python objects.
///
/// Each path is a sequence of map keys, e.g. `["country", "*", "name"]`.
/// A subtree is converted in full once a path ends at it. Map entries that no
/// path leads to are skipped entirely, so they are never marshalled.
#[derive(Debug, Default, PartialEq)]
pub struct KeyPathFilter {
    /// A path ends at this node: select the whole subtree.
    include_all: bool,
    children: HashMap<String, KeyPathFilter>,
    wildcard: Option<Box<KeyPathFilter>>,
}

impl KeyPathFilter {
    /// Build a filter from a list of paths. An empty path selects everything.
    pub fn from_paths<P, S>(paths: P) -> KeyPathFilter
    where
        P: IntoIterator,
        P::Item: IntoIterator<Item=S>,
        S: Into<String>,
    {
        let mut root = KeyPathFilter::default();
        for path in paths {
            root.insert(path.into_iter().map(Into::into));
        }
        root.normalize();
        root
    }

    fn insert(&mut self, mut path: impl Iterator<Item=String>) {
        let mut node = self;
        loop {
            if node.include_all {
                return;  // an existing, shorter path already selects this subtree
            }
            match path.next() {
                None => {
                    node.include_all = true;
                    node.children.clear();
                    node.wildcard = None;
                    return;
                }
                Some(key) if key == WILDCARD => {
                    node = node.wildcard.get_or_insert_with(Default::default);
                }
                Some(key) => {
                    node = node.children.entry(key).or_default();
                }
            }
        }
    }

    /// Add everything selected by `other` to `self`.
    fn merge(&mut self, other: &KeyPathFilter) {
        if self.include_all {
            return;
        }
        if other.include_all {
            self.include_all = true;
            self.children.clear();
            self.wildcard = None;
            return;
        }
        for (key, other_child) in other.children.iter() {
            self.children.entry(key.clone()).or_default().merge(other_child);
        }
        if let Some(other_wildcard) = &other.wildcard {
            self.wildcard.get_or_insert_with(Default::default).merge(other_wildcard);
        }
    }

    /// Fold the wildcard subtree into each explicit child, so that `child()` can return
    /// a single node for keys matched both by name and by a wildcard
    /// (e.g. `country.*.name` together with `country.0.flag`).
    fn normalize(&mut self) {
        if let Some(wildcard) = &self.wildcard {
            for child in self.children.values_mut() {
                child.merge(wildcard);
            }
        }
        for child in self.children.values_mut() {
            child.normalize();
        }
        if let Some(wildcard) = &mut self.wildcard {
            wildcard.normalize();
        }
    }

    /// True if the whole subtree at this node is selected.
    pub fn selects_all(&self) -> bool {
        self.include_all
    }

    /// The filter for the value stored under `key`, or None if no path leads there.
    pub fn child(&self, key: &str) -> Option<&KeyPathFilter> {
        self.children.get(key).or(self.wildcard.as_deref())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn filter(paths: &[&[&str]]) -> KeyPathFilter {
        KeyPathFilter::from_paths(paths.iter().map(|p| p.iter().copied()))
    }

    #[test]
    fn test_no_paths_selects_nothing() {
        let f = filter(&[]);
        assert!(!f.selects_all());
        assert!(f.child("country").is_none());
    }

    #[test]
    fn test_empty_path_selects_everything() {
        let f = filter(&[&["country"], &[]]);
        assert!(f.selects_all());
    }

    #[test]
    fn test_simple_paths() {
        let f = filter(&[&["country"], &["galaxy", "name"]]);
        assert!(!f.selects_all());
        assert!(f.child("country").unwrap().selects_all());
        let galaxy = f.child("galaxy").unwrap();
        assert!(!galaxy.selects_all());
        assert!(galaxy.child("name").unwrap().selects_all());
        assert!(galaxy.child("width").is_none());
        assert!(f.child("fleet").is_none());
    }

    #[test]
    fn test_shorter_path_wins() {
        assert_eq!(
            filter(&[&["country", "0", "name"], &["country"]]),
            filter(&[&["country"]]),
        );
        assert_eq!(
            filter(&[&["country"], &["country", "0", "name"]]),
            filter(&[&["country"]]),
        );
    }

    #[test]
    fn test_wildcard() {
        let f = filter(&[&["country", "*", "name"], &["country", "0", "flag"]]);
        let country = f.child("country").unwrap();

        let c0 = country.child("0").unwrap();
        assert!(c0.child("name").unwrap().selects_all());
        assert!(c0.child("flag").unwrap().selects_all());
        assert!(c0.child("budget").is_none());

        let c1 = country.child("1").unwrap();
        assert!(c1.child("name").unwrap().selects_all());
        assert!(c1.child("flag").is_none());
    }

    #[test]
    fn test_nested_wildcards() {
        let f = filter(&[&["a", "*", "*", "x"], &["a", "k", "*", "y"], &["a", "k", "m"]]);
        let k = f.child("a").unwrap().child("k").unwrap();
        assert!(k.child("m").unwrap().selects_all());
        let other = k.child("other").unwrap();
        assert!(other.child("x").unwrap().selects_all());
        assert!(other.child("y").unwrap().selects_all());
        let j_other = f.child("a").unwrap().child("j").unwrap().child("other").unwrap();
        assert!(j_other.child("x").unwrap().selects_all());
        assert!(j_other.child("y").is_none());
    }
}
