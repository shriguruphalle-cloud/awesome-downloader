"""The paste boxes hand each other whatever isn't theirs.

See app/core/link_router.py for how a link is classified. A link that
belongs in another tab opens there (that tab comes to the front); when one
link holds both kinds -- an Instagram account, its stories, a carousel --
each tab gets its share, and the one not in view gets a dot.
"""


def wire(win, video_tab, images_tab, torrent_tab):
    video_i = win.tabs.indexOf(video_tab)
    images_i = win.tabs.indexOf(images_tab)
    torrent_i = win.tabs.indexOf(torrent_tab) if torrent_tab is not None else -1

    def show_or_badge(index, switch):
        if switch:
            win.tabs.setCurrentIndex(index)
        elif win.tabs.currentIndex() != index:
            win.set_tab_badge(index, True)

    def route(target, urls):
        urls = list(urls or [])
        if not urls:
            return
        if target == "video":
            win.tabs.setCurrentIndex(video_i)
            if len(urls) == 1:
                video_tab.open_link(urls[0])
            else:
                video_tab.queue_links(urls)
        elif target == "images":
            win.tabs.setCurrentIndex(images_i)
            images_tab.fetch_links(urls)
        elif target == "torrent" and torrent_tab is not None:
            win.tabs.setCurrentIndex(torrent_i)
            for u in urls:
                if u.lower().startswith("magnet:"):
                    torrent_tab.magnet_forwarded.emit(u)
                else:
                    torrent_tab.add_torrent_url(u)

    video_tab.send_to_tab.connect(route)
    images_tab.send_to_tab.connect(route)
    video_tab.images_found.connect(
        lambda title, items, note, switch, append: (images_tab.receive_items(title, items, note, append),
                                                    show_or_badge(images_i, switch)))
    images_tab.videos_found.connect(
        lambda title, entries, note, switch: (video_tab.stack_found(title, entries, note),
                                              show_or_badge(video_i, switch)))
    return route
