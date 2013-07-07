// replace all help text <p> with <img> of question mark (a nicer and more compact way to show help text)
django.jQuery(function($) {
    $("div p.help").replaceWith(function () {
        var helptext = $(this).text();
        return "<img src='/static/admin/img/icon-unknown.gif' class='help help-tooltip' width='10' height='10' alt='"+helptext+"' title='"+helptext+"'/>";
    });
}, django.jQuery);
