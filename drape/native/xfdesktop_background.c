/* Draw Drape's owned pixmap behind Xfdesktop's icons.
 *
 * Only public GTK/GDK/Cairo ABI functions are used. Resolving them at runtime
 * avoids requiring development headers or linking to private Xfdesktop fields.
 * This module is loaded into one explicitly authorized desktop process only.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef struct List {
    void *data;
    struct List *next, *prev;
} List;

typedef struct Widget {
    void *widget;
    struct Widget *next;
} Widget;

static Widget *widgets;
static unsigned long previous;
static int initialized;

/* Opaque pointers refer to public GTK, GDK and Cairo objects. */
#define FN(result, name, args) static result (*name) args
FN(void, real_show, (void *));
FN(const char *, type_name, (void *));
FN(unsigned long, connect_data, (void *, const char *, void *, void *, void *, int));
FN(unsigned int, timeout_add, (unsigned int, void *, void *));
FN(void *, get_window, (void *));
FN(void *, get_display, (void *));
FN(void *, default_display, (void));
FN(int, change_property, (void *, unsigned long, unsigned long, unsigned long,
                         int, int, const unsigned char *, int));
FN(void *, get_xdisplay, (void *));
FN(void *, get_visual, (void *));
FN(void *, get_xvisual, (void *));
FN(int, get_origin, (void *, int *, int *));
FN(void, queue_draw, (void *));
FN(List *, get_children, (void *));
FN(void, free_list, (List *));
FN(void, propagate_draw, (void *, void *, void *));
FN(void, trap_push, (void *));
FN(void, trap_pop, (void *));
FN(unsigned long, root_window, (void *));
FN(unsigned long, intern_atom, (void *, const char *, int));
FN(int, get_property, (void *, unsigned long, unsigned long, long, long, int,
                      unsigned long, unsigned long *, int *, unsigned long *,
                      unsigned long *, unsigned char **));
FN(int, x_free, (void *));
FN(void *, create_surface, (void *, unsigned long, void *, int, int));
FN(int, surface_status, (void *));
FN(void, destroy_surface, (void *));
FN(void, save, (void *));
FN(void, restore, (void *));
FN(void, set_operator, (void *, int));
FN(void, set_source, (void *, void *, double, double));
FN(void, paint, (void *));
FN(int, get_geometry, (void *, unsigned long, unsigned long *, int *, int *,
                      unsigned int *, unsigned int *, unsigned int *, unsigned int *));

static unsigned long property(void *display, const char *name, unsigned long expected)
{
    unsigned long actual = 0, count = 0, remaining = 0, value = 0;
    int format = 0;
    unsigned char *data = NULL;
    unsigned long atom = intern_atom(display, name, 1);
    if (!atom)
        return 0;
    if (!get_property(display, root_window(display), atom, 0, 1, 0, expected,
                      &actual, &format, &count, &remaining, &data)
        && actual == expected && format == 32 && count == 1 && data)
        value = *((unsigned long *)data);
    if (data)
        x_free(data);
    return value;
}

/* A crashed publisher must reveal the original background even if stale
 * X properties survive its connection. Never pass a group ID to kill(). */
static int publisher_alive(void *display)
{
    unsigned long owner = property(display, "_DRAPE_LIVE_WALLPAPER_OWNER", 6);
    if (!owner || owner > INT_MAX)
        return 0;
    return !kill((pid_t)owner, 0) || errno == EPERM;
}

static int draw(void *widget, void *cr, void *unused)
{
    (void)unused;
    void *window = get_window(widget);
    if (!window)
        return 0;
    void *gdpy = get_display(window), *display = get_xdisplay(gdpy);
    if (!publisher_alive(display))
        return 0;
    unsigned long pixmap = property(display, "_DRAPE_LIVE_WALLPAPER_PIXMAP", 20);
    if (!pixmap)
        return 0;

    trap_push(gdpy);
    unsigned long root;
    int xx, yy;
    unsigned int width, height, border, depth;
    if (!get_geometry(display, pixmap, &root, &xx, &yy, &width, &height, &border, &depth)) {
        trap_pop(gdpy);
        return 0;
    }
    void *surface = create_surface(display, pixmap, get_xvisual(get_visual(window)), width, height);
    if (surface_status(surface)) {
        destroy_surface(surface);
        trap_pop(gdpy);
        return 0;
    }
    get_origin(window, &xx, &yy);
    save(cr);
    set_operator(cr, 1); /* CAIRO_OPERATOR_SOURCE */
    set_source(cr, surface, -xx, -yy);
    paint(cr);
    restore(cr);
    destroy_surface(surface);
    trap_pop(gdpy);

    /* Replace only the backdrop paint; icons retain their normal drawing. */
    List *children = get_children(widget);
    for (List *child = children; child; child = child->next)
        propagate_draw(widget, child->data, cr);
    free_list(children);
    return 1;
}

static void destroyed(void *widget, void *unused)
{
    (void)unused;
    Widget **entry = &widgets;
    while (*entry) {
        if ((*entry)->widget == widget) {
            Widget *old = *entry;
            *entry = old->next;
            free(old);
            break;
        }
        entry = &(*entry)->next;
    }
}

/* One serial covers all monitors. Pause and idle audio leave it unchanged. */
static int tick(void *unused)
{
    (void)unused;
    if (!widgets)
        return 1;
    void *window = get_window(widgets->widget);
    if (!window)
        return 1;
    void *display = get_xdisplay(get_display(window));
    unsigned long frame = publisher_alive(display)
        ? property(display, "_DRAPE_LIVE_WALLPAPER_FRAME", 6) : 0;
    if (frame != previous) {
        previous = frame;
        for (Widget *entry = widgets; entry; entry = entry->next)
            queue_draw(entry->widget);
    }
    return 1;
}

#define LOAD(target, symbol) do { \
    *(void **)(&target) = dlsym(RTLD_DEFAULT, symbol); \
    if (!target) return; \
} while (0)

static void init(void)
{
    LOAD(type_name, "g_type_name_from_instance");
    LOAD(connect_data, "g_signal_connect_data");
    LOAD(timeout_add, "g_timeout_add");
    LOAD(get_window, "gtk_widget_get_window");
    LOAD(get_display, "gdk_window_get_display");
    LOAD(get_xdisplay, "gdk_x11_display_get_xdisplay");
    LOAD(default_display, "gdk_display_get_default");
    LOAD(change_property, "XChangeProperty");
    LOAD(get_visual, "gdk_window_get_visual");
    LOAD(get_xvisual, "gdk_x11_visual_get_xvisual");
    LOAD(get_origin, "gdk_window_get_origin");
    LOAD(queue_draw, "gtk_widget_queue_draw");
    LOAD(get_children, "gtk_container_get_children");
    LOAD(free_list, "g_list_free");
    LOAD(propagate_draw, "gtk_container_propagate_draw");
    LOAD(trap_push, "gdk_x11_display_error_trap_push");
    LOAD(trap_pop, "gdk_x11_display_error_trap_pop_ignored");
    LOAD(root_window, "XDefaultRootWindow");
    LOAD(intern_atom, "XInternAtom");
    LOAD(get_property, "XGetWindowProperty");
    LOAD(x_free, "XFree");
    LOAD(create_surface, "cairo_xlib_surface_create");
    LOAD(surface_status, "cairo_surface_status");
    LOAD(destroy_surface, "cairo_surface_destroy");
    LOAD(save, "cairo_save");
    LOAD(restore, "cairo_restore");
    LOAD(set_operator, "cairo_set_operator");
    LOAD(set_source, "cairo_set_source_surface");
    LOAD(paint, "cairo_paint");
    LOAD(get_geometry, "XGetGeometry");
    initialized = 1;
    timeout_add(33, (void *)tick, NULL);
}

static void attach(void *widget)
{
    if (!initialized)
        init();
    if (!initialized || !widget || strcmp(type_name(widget), "XfceDesktop"))
        return;
    for (Widget *entry = widgets; entry; entry = entry->next)
        if (entry->widget == widget)
            return;
    Widget *entry = malloc(sizeof(*entry));
    if (!entry)
        return;
    entry->widget = widget;
    entry->next = widgets;
    widgets = entry;
    connect_data(widget, "draw", (void *)draw, NULL, NULL, 0);
    connect_data(widget, "destroy", (void *)destroyed, NULL, NULL, 0);
    void *gdpy = default_display();
    if (gdpy) {
        void *display = get_xdisplay(gdpy);
        unsigned long owner = (unsigned long)getpid();
        unsigned long atom = intern_atom(display, "_DRAPE_XFDESKTOP_ADAPTER", 0);
        change_property(display, root_window(display), atom, 6, 32, 0,
                        (const unsigned char *)&owner, 1);
    }
}

/* Xfdesktop shows its icon host with show_all; plain show is also supported. */
void gtk_widget_show(void *widget)
{
    if (!real_show)
        *(void **)(&real_show) = dlsym(RTLD_NEXT, "gtk_widget_show");
    attach(widget);
    if (real_show)
        real_show(widget);
}

void gtk_widget_show_all(void *widget)
{
    static void (*real_show_all)(void *);
    if (!real_show_all)
        *(void **)(&real_show_all) = dlsym(RTLD_NEXT, "gtk_widget_show_all");
    attach(widget);
    if (real_show_all)
        real_show_all(widget);
}
